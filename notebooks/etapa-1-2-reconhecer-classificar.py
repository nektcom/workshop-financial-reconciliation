"""
Etapa 1 + 2 (invoices) — MUTIRAO.
1) pre-filtro dos anexos (candidato vs ruido); 2) IA le o CONTEUDO do anexo (Volume Attachments)
e classifica (escopo: NF/NFS-e/NFC-e/DANFE/invoice/recibo/passagem; FORA: imposto, boleto,
contrato, reembolso/nota de debito). Roda a IA em paralelo. Grava Trusted.invoices_classificadas.

>>> Antes de rodar, troque todos os placeholders <ASSIM> pelos seus valores (ver README). <<<
"""
import json, base64, re
import pandas as pd
import nekt, fsspec
from litellm import completion
from concurrent.futures import ThreadPoolExecutor

log = nekt.get_logger()
MODEL = "<MODELO_LLM>"            # ex.: "anthropic/claude-...", "gemini/...", "openai/gpt-..."
BASE = "<URL_DO_PROXY_LLM>"       # gateway de LLM (LiteLLM, etc.) ou API do provedor
SECRET = "<NOME_DO_SECRET_LLM>"   # secret na Nekt com a chave da API
MAX_WORKERS = 8

PROMPT = """Analise este DOCUMENTO (anexo de um email do contas a pagar de uma empresa) e classifique pelo conteudo.
E VALIDO (is_fiscal=true) se for comprovante de uma COMPRA/DESPESA da empresa:
- Nota fiscal (NF, NFS-e, NFC-e, NFe, DANFE), invoice, recibo;
- Comprovante de compra, INCLUSIVE PASSAGENS (aviao, onibus, etc.) e bilhetes (servem como comprovante da compra).
NAO e valido (is_fiscal=false) — fora de escopo:
- Guias de imposto: DARF, DAS, ISS, PIS, COFINS, INSS, IRPJ, CSLL e demonstrativos de imposto;
- Boleto sem nota, extrato/carta de banco;
- Contrato, termo, NDA, documento assinado;
- Reembolso, nota de debito, adiantamento ou estorno (nao sao compra no cartao);
- Marketing, newsletter, convite de agenda, assinatura de email, logo.
Extraia tambem o NUMERO/codigo UNICO do documento: numero da NF, "Invoice number", "Recibo no", id/numero da fatura. Copie exatamente como aparece (com prefixos/zeros); se realmente nao houver, deixe "".
Responda APENAS um JSON valido (sem markdown):
{"is_fiscal": true/false, "tipo_documento": "nota_fiscal|invoice|recibo|passagem|outro|", "fornecedor": "", "numero": "", "moeda": "", "valor": "", "data": "AAAA-MM-DD ou ''", "confianca": "alta|media|baixa", "motivo": "frase curta"}"""

def to_int(x):
    try:
        return int(x)
    except Exception:
        return 0

def ext_of(name):
    m = re.search(r"\.([A-Za-z0-9]+)$", name or "")
    return m.group(1).lower() if m else ""

def categorize(nome, ext, sz):
    nl = (nome or "").lower()
    sz = to_int(sz)
    if ext in ("ics", "eml", "txt") or ext == "":
        return "ruido_agenda_inline_texto"
    if nl.startswith("outlook-"):
        return "ruido_imagem_inline"
    if re.search(r"assinatura|signature|template", nl):
        return "ruido_assinatura"
    if ext in ("png", "jpg", "jpeg") and sz < 20480:
        return "ruido_imagem_pequena"
    if ext == "zip":
        return "rever_zip"
    if ext == "docx":
        return "rever_docx"
    if ext in ("pdf", "png", "jpg", "jpeg", "xml"):
        return "candidato"
    return "rever_outro"

def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())

# ---- carregar anexos + categorizar ----
att = nekt.load_table(layer_name="Raw", table_name="email_attachments")
att["ext"] = att["_nekt_file_name"].apply(ext_of)
att["categoria"] = att.apply(lambda r: categorize(r["_nekt_file_name"], r["ext"], r["_nekt_file_size"]), axis=1)

# ---- Volume: indexar por message_id ----
vfiles = nekt.load_volume(layer_name="Raw", volume_name="Attachments")
vol_by_msg = {}
for it in vfiles:
    msg = str(it.get("name", "")).split("_", 1)[0]
    vol_by_msg.setdefault(msg, []).append(it)

def find_path(message_id, file_name, size):
    items = vol_by_msg.get(str(message_id), [])
    sz = to_int(size)
    by_size = [it for it in items if to_int(it.get("file_size")) == sz]
    if len(by_size) == 1:
        return by_size[0]["s3_path"], "by_msg_size"
    tgt = norm(file_name)
    by_name = [it for it in items if norm(it.get("name", "")).endswith(tgt)]
    if len(by_name) == 1:
        return by_name[0]["s3_path"], "by_msg_nome"
    if by_size:
        return by_size[0]["s3_path"], f"by_size_multi({len(by_size)})"
    if by_name:
        return by_name[0]["s3_path"], f"by_nome_multi({len(by_name)})"
    return None, f"NAO_ENCONTRADO(msg={len(items)})"

api_key = nekt.load_secret(key=SECRET)

def classify(b, ext, mime):
    if ext == "xml":
        txt = b.decode("utf-8", errors="ignore")[:8000]
        content = [{"type": "text", "text": PROMPT + "\n\nConteudo XML:\n" + txt}]
    else:
        b64 = base64.standard_b64encode(b).decode("utf-8")
        content = [{"type": "text", "text": PROMPT},
                   {"type": "image_url", "image_url": {"url": f"data:{mime or 'application/pdf'};base64,{b64}"}}]
    resp = completion(model=MODEL, messages=[{"role": "user", "content": content}],
                      api_key=api_key, api_base=BASE, timeout=180, max_tokens=2000, reasoning_effort="low")
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(raw)

# ---- candidatos distintos para a IA (dedup por nome+tamanho) ----
cand = att[att["categoria"] == "candidato"].copy()
distinct = cand.drop_duplicates(subset=["_nekt_file_name", "_nekt_file_size"])
log.info("Anexos: %d | candidatos: %d | distintos p/ IA: %d", len(att), len(cand), len(distinct))

def work(row):
    key = (row["_nekt_file_name"], to_int(row["_nekt_file_size"]))
    path, how = find_path(row["message_id"], row["_nekt_file_name"], row["_nekt_file_size"])
    rec = {"match": how, "s3_path": path}
    if path:
        try:
            with fsspec.open(path, "rb") as fh:
                b = fh.read()
            rec["ia"] = classify(b, row["ext"], row["_nekt_mime_type"])
        except Exception as e:
            rec["erro"] = repr(e)
    return key, rec

results = {}
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
    for key, rec in ex.map(lambda kv: work(kv[1]), distinct.iterrows()):
        results[key] = rec

# ---- montar tabela final (todos os anexos) ----
def g(d, k):
    return d.get(k) if isinstance(d, dict) else None

rows_out = []
for _, r in att.iterrows():
    res = results.get((r["_nekt_file_name"], to_int(r["_nekt_file_size"]))) if r["categoria"] == "candidato" else None
    ia = g(res, "ia") if res else None
    rows_out.append({
        "message_id": r["message_id"], "arquivo": r["_nekt_file_name"], "ext": r["ext"],
        "mime": r["_nekt_mime_type"], "tamanho_bytes": to_int(r["_nekt_file_size"]), "categoria": r["categoria"],
        "ia_is_fiscal": g(ia, "is_fiscal"), "ia_tipo": g(ia, "tipo_documento"), "ia_fornecedor": g(ia, "fornecedor"),
        "ia_numero": g(ia, "numero"),
        "ia_moeda": g(ia, "moeda"), "ia_valor": g(ia, "valor"), "ia_data": g(ia, "data"),
        "ia_confianca": g(ia, "confianca"), "ia_motivo": g(ia, "motivo"),
        "s3_path": g(res, "s3_path"), "status_leitura": g(res, "match"), "erro_leitura": g(res, "erro"),
        "email_date": str(r.get("internalDate")),
    })

df = pd.DataFrame(rows_out)
df["ia_is_fiscal"] = df["ia_is_fiscal"].astype("object")
nekt.save_table(df=df, layer_name="Trusted", table_name="invoices_classificadas",
                folder_name="Conciliacao", mode="overwrite")

fiscais = df[df["ia_is_fiscal"] == True]
log.info("GRAVADO %d linhas. Documentos fiscais (manter): %d", len(df), len(fiscais))
print("OK")
