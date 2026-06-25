"""
Etapa 7 (cartao) — le os PDFs de fatura do Volume, extrai os lancamentos com IA de visao,
mascara portadores, valida cada banco contra o total da fatura e GRAVA Trusted.transacoes_cartao.
TRAVA DE SEGURANCA: so grava se TODOS os bancos baterem (auditoria).

>>> Antes de rodar, troque todos os placeholders <ASSIM> pelos seus valores (ver README). <<<
"""
import json, base64, re
import pandas as pd
import nekt, fsspec
from litellm import completion

log = nekt.get_logger()
LAYER = "Raw"
VOLUME = "<NOME_DO_VOLUME_COM_AS_FATURAS>"
MODEL = "<MODELO_LLM>"
BASE = "<URL_DO_PROXY_LLM>"
SECRET = "<NOME_DO_SECRET_LLM>"

DEST_LAYER, DEST_TABLE, DEST_FOLDER = "Trusted", "transacoes_cartao", "Conciliacao"

# Total de CADA fatura (a trava so grava se a soma extraida bater com isto).
# TROQUE os 0.00 pelo valor total de cada fatura, e "<BANCO_N>" pelo nome do banco.
EXPECTED = {
    "<BANCO_1>": 0.00,   # <- total da fatura do banco 1
    "<BANCO_2>": 0.00,   # <- total da fatura do banco 2
    "<BANCO_3>": 0.00,   # <- total da fatura do banco 3
}
COMPANY_TOKENS = ("LTDA", "S.A", "S/A", "EIRELI", "<NOME_DA_SUA_EMPRESA>")

# Mapeie um trecho do NOME DO ARQUIVO da fatura -> nome do banco.
BANK_KEYWORDS = {"<palavra_no_nome_do_arquivo_1>": "<BANCO_1>",
                 "<palavra_no_nome_do_arquivo_2>": "<BANCO_2>",
                 "<palavra_no_nome_do_arquivo_3>": "<BANCO_3>"}

def detect_bank(name):
    n = name.lower()
    for kw, bank in BANK_KEYWORDS.items():
        if kw in n:
            return bank
    return "?"

def parse_meta(name):
    """competencia (AAAA-MM) e vencimento (AAAA-MM-DD) a partir do nome do arquivo."""
    venc = ""
    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", name)
    if m:
        venc = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    comp = ""
    m2 = re.search(r"(\d{4})(\d{2})-\d{2}\s*-", name)
    if m2:
        comp = f"{m2.group(1)}-{m2.group(2)}"
    return comp, venc

def is_company(name):
    up = name.upper()
    return any(tok in up for tok in COMPANY_TOKENS)

PROMPT = """Voce recebe o PDF de uma FATURA DE CARTAO DE CREDITO brasileira (banco: {banco}).
Extraia TODOS os lancamentos de TODOS os cartoes/portadores da fatura: compras nacionais, compras internacionais, pagamento(s) da fatura anterior, IOF e encargos. Nao resuma nem agregue — cada linha de movimentacao vira um objeto.
Para cada lancamento devolva um objeto com:
- "data": AAAA-MM-DD (use o ano da fatura; se so houver dia/mes, infira)
- "portador": nome da PESSOA FISICA portadora do cartao se aparecer; se so aparecer o nome da empresa (ex.: <SUA EMPRESA> LTDA), use ""
- "cartao_final": 4 ultimos digitos do cartao se aparecer, senao ""
- "estabelecimento": descricao como aparece
- "moeda_origem": "USD" se houver valor em dolar, senao "BRL"
- "valor_origem": numero na moeda original, ou null
- "cotacao": cotacao do dolar (numero), ou null
- "valor_brl": valor em reais (numero). Pagamentos/estornos devem vir NEGATIVOS.
- "parcela": "X/Y" se parcelada, senao ""
- "tipo": "compra", "pagamento" ou "imposto" (IOF/encargos="imposto"; pagamento de fatura="pagamento")
IMPORTANTE: responda em JSON COMPACTO, numa unica linha, SEM espacos extras nem quebras de linha. Apenas a lista de objetos, sem markdown."""

def extrair(banco, data_bytes, api_key):
    b64 = base64.standard_b64encode(data_bytes).decode("utf-8")
    resp = completion(
        model=MODEL,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": PROMPT.format(banco=banco)},
            {"type": "image_url", "image_url": {"url": f"data:application/pdf;base64,{b64}"}},
        ]}],
        api_key=api_key, api_base=BASE, timeout=300, max_tokens=16000, reasoning_effort="low",
    )
    content = getattr(resp.choices[0].message, "content", None)
    if not content:
        raise RuntimeError("IA retornou vazio")
    raw = content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(raw)

api_key = nekt.load_secret(key=SECRET)
files = nekt.load_volume(layer_name=LAYER, volume_name=VOLUME)

all_rows, resumo = [], {}
for f in files:
    banco = detect_bank(f["name"])
    comp, venc = parse_meta(f["name"])
    with fsspec.open(f["s3_path"], "rb") as fh:
        data = fh.read()
    lancs = extrair(banco, data, api_key)
    for r in lancs:
        r["banco"] = banco; r["competencia"] = comp; r["vencimento_fatura"] = venc; r["arquivo_origem"] = f["name"]
    all_rows.extend(lancs)
    dfb = pd.DataFrame(lancs)
    tot = round(dfb[dfb["tipo"].isin(["compra", "imposto"])]["valor_brl"].astype(float).sum(), 2)
    esp = EXPECTED.get(banco)
    resumo[banco] = {"n": len(lancs), "total": tot, "esperado": esp, "ok": esp is not None and abs(tot - esp) < 0.01}

# Mascarar portadores: nome distinto -> "Portador A/B/C/..."
def norm(p):
    p = " ".join((p or "").split()).strip()
    if not p or is_company(p):
        return ""
    return p.title()

nomes = []
for r in all_rows:
    p = norm(r.get("portador"))
    if p and p not in nomes:
        nomes.append(p)
mapa = {nome: f"Portador {chr(65 + i)}" for i, nome in enumerate(nomes)}
for r in all_rows:
    r["portador"] = mapa.get(norm(r.get("portador")), "")

# TRAVA: so grava se TODOS os bancos baterem com a fatura
log.info("Validacao por banco: %s", resumo)
if not all(v["ok"] for v in resumo.values()):
    raise RuntimeError("Validacao FALHOU em algum banco — NAO vou gravar. " + json.dumps(resumo, ensure_ascii=False))

df = pd.DataFrame(all_rows).rename(columns={"data": "data_transacao"})
df = df[["banco", "competencia", "vencimento_fatura", "portador", "cartao_final", "data_transacao",
         "estabelecimento", "moeda_origem", "valor_origem", "cotacao", "valor_brl", "parcela", "tipo", "arquivo_origem"]]
nekt.save_table(df=df, layer_name=DEST_LAYER, table_name=DEST_TABLE, folder_name=DEST_FOLDER, mode="overwrite")
print(f"OK — {len(df)} linhas gravadas em {DEST_LAYER}.{DEST_TABLE}")
