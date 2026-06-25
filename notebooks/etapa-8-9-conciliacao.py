"""
Etapa 8+9 — CONCILIACAO.
- 1:1 por valor exato (mesma moeda, data +-12d) OU moeda convertida (USD<->BRL, razao 4.5-6.0, data +-12d).
- Nota SEM DATA casa por valor IGUAL + fornecedor IDENTICO (normalizado), sem janela (regra estrita).
- Fornecedores recorrentes/pulverizados: conciliacao AGREGADA por competencia.
Grava Trusted.conciliacao e Trusted.conciliacao_agregada.

>>> Antes de rodar, troque todos os placeholders <ASSIM> pelos seus valores (ver README). <<<
"""
import re
import pandas as pd
from difflib import SequenceMatcher
import nekt

log = nekt.get_logger()
WINDOW = 12
RATIO_MIN, RATIO_MAX = 4.5, 6.0

def canon_agg(name):
    # Fornecedores recorrentes/pulverizados que se concilia POR COMPETENCIA (varias cobrancas no mes).
    # Adapte aos seus (ex.: anuncios, nuvem por consumo).
    s = str(name or "").lower()
    if "facebk" in s or "facebook" in s:
        return "Meta/Facebook"
    if "google ads" in s or "google brasil" in s:
        return "Google Ads"
    return None

def is_agg(x):
    return isinstance(x, str) and x != ""

def norm_nome(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())

cc = nekt.load_table(layer_name="Trusted", table_name="transacoes_cartao")
inv = nekt.load_table(layer_name="Trusted", table_name="invoices")

compras = cc[cc["tipo"] == "compra"].reset_index(drop=True).copy()
compras["moeda_cmp"] = compras["moeda_origem"].astype(str)
compras["valor_cmp"] = compras.apply(
    lambda r: r["valor_origem"] if str(r["moeda_origem"]) == "USD" else r["valor_brl"], axis=1)
compras["valor_cmp"] = pd.to_numeric(compras["valor_cmp"], errors="coerce")
compras["data_cmp"] = pd.to_datetime(compras["data_transacao"], errors="coerce")
compras["agg"] = compras["estabelecimento"].apply(canon_agg)

inv = inv.drop_duplicates(subset=["drive_link"]).reset_index(drop=True).copy()
inv["valor_cmp"] = pd.to_numeric(inv["valor"], errors="coerce")
inv["moeda_cmp"] = inv["moeda"].astype(str)
inv["data_cmp"] = pd.to_datetime(inv["data_doc"], errors="coerce")
inv["agg"] = inv["fornecedor"].apply(canon_agg)

def sim(a, b):
    return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()

def brand(name):
    s = str(name or "").lower()
    # Apelidos: fornecedor que aparece com nome diferente no cartao vs na nota.
    ALIASES = {"<APELIDO_NO_CARTAO>": "<NOME_CANONICO>"}  # ex.: {"claude": "anthropic"}
    for k, v in ALIASES.items():
        if k in s:
            return v
    toks = re.findall(r"[a-z0-9]+", s)
    return toks[0] if toks else s

def mesmo_fornecedor(e, f):
    return brand(e) == brand(f) or sim(e, f) >= 0.6

def dias(a, b):
    return abs((a - b).days) if pd.notna(a) and pd.notna(b) else 999

cands = []
for i, c in compras.iterrows():
    if is_agg(c["agg"]) or pd.isna(c["valor_cmp"]):
        continue
    cv = round(float(c["valor_cmp"]), 2)
    for j, v in inv.iterrows():
        if is_agg(v["agg"]) or pd.isna(v["valor_cmp"]):
            continue
        dd = dias(c["data_cmp"], v["data_cmp"])
        same_exact = (v["moeda_cmp"] == c["moeda_cmp"] and cv == round(float(v["valor_cmp"]), 2))
        ns = -sim(c["estabelecimento"], v["fornecedor"])
        if same_exact and dd <= WINDOW:
            cands.append((0, dd, ns, i, j, "valor"))
        elif c["moeda_cmp"] == "USD" and v["moeda_cmp"] == "BRL" and float(c["valor_cmp"]) > 0 and dd <= WINDOW:
            ratio = float(v["valor_cmp"]) / float(c["valor_cmp"])
            if RATIO_MIN <= ratio <= RATIO_MAX and mesmo_fornecedor(c["estabelecimento"], v["fornecedor"]):
                cands.append((1, dd, ns, i, j, "moeda_convertida"))
        elif same_exact and pd.isna(v["data_cmp"]) and norm_nome(c["estabelecimento"]) == norm_nome(v["fornecedor"]):
            cands.append((2, 999, ns, i, j, "valor_sem_data"))

cands.sort(key=lambda x: (x[0], x[1], x[2]))
used_c, used_i, pares = set(), set(), {}
for prio, dd, nsim, i, j, regra in cands:
    if i in used_c or j in used_i:
        continue
    used_c.add(i); used_i.add(j); pares[i] = (j, dd, -nsim, regra)

rows = []
for i, c in compras.iterrows():
    status = "agregado" if is_agg(c["agg"]) else ("conciliado" if i in pares else "nao_conciliado")
    rec = {"tipo_registro": "compra", "status": status, "banco": c["banco"], "portador": c["portador"],
           "cartao_final": c["cartao_final"], "data_cartao": c["data_transacao"], "estabelecimento": c["estabelecimento"],
           "moeda": c["moeda_cmp"], "valor": round(float(c["valor_cmp"]), 2) if pd.notna(c["valor_cmp"]) else None,
           "valor_brl": c["valor_brl"], "parcela": c["parcela"],
           "fornecedor_agregado": c["agg"] if is_agg(c["agg"]) else None,
           "nota_fornecedor": None, "nota_valor": None, "nota_data": None, "nota_tipo": None,
           "nota_link": None, "dias_diff": None, "sim_nome": None, "regra_match": None}
    if i in pares:
        j, dd, nsim, regra = pares[i]
        v = inv.loc[j]
        rec.update({"nota_fornecedor": v["fornecedor"], "nota_valor": v["valor"], "nota_data": v["data_doc"],
                    "nota_tipo": v["tipo"], "nota_link": v["drive_link"], "dias_diff": dd,
                    "sim_nome": round(nsim, 2), "regra_match": regra})
    rows.append(rec)

for j, v in inv.iterrows():
    if is_agg(v["agg"]) or j in used_i:
        continue
    rows.append({"tipo_registro": "nota_sem_compra", "status": "nao_conciliado", "banco": None, "portador": None,
                 "cartao_final": None, "data_cartao": None, "estabelecimento": None, "moeda": v["moeda_cmp"],
                 "valor": round(float(v["valor_cmp"]), 2) if pd.notna(v["valor_cmp"]) else None, "valor_brl": None,
                 "parcela": None, "fornecedor_agregado": None, "nota_fornecedor": v["fornecedor"],
                 "nota_valor": v["valor"], "nota_data": v["data_doc"], "nota_tipo": v["tipo"],
                 "nota_link": v["drive_link"], "dias_diff": None, "sim_nome": None, "regra_match": None})

res = pd.DataFrame(rows)
nekt.save_table(df=res, layer_name="Trusted", table_name="conciliacao", folder_name="Conciliacao", mode="overwrite")

# Bloco AGREGADO por competencia (cobrado x notas x diferenca)
ca = compras[compras["agg"].apply(is_agg)].copy()
ca["competencia"] = ca["data_transacao"].astype(str).str.slice(0, 7)
g_cart = ca.groupby(["agg", "competencia"]).agg(
    total_cartao_brl=("valor_brl", lambda s: round(pd.to_numeric(s, errors="coerce").sum(), 2)),
    n_cobrancas=("valor_brl", "size")).reset_index()
ia = inv[inv["agg"].apply(is_agg)].copy()
ia["competencia"] = ia["data_doc"].astype(str).str.slice(0, 7)
g_nota = ia.groupby(["agg", "competencia"]).agg(
    total_notas_brl=("valor_cmp", lambda s: round(pd.to_numeric(s, errors="coerce").sum(), 2)),
    n_notas=("valor_cmp", "size")).reset_index()
agg = pd.merge(g_cart, g_nota, on=["agg", "competencia"], how="outer").fillna(
    {"total_cartao_brl": 0, "n_cobrancas": 0, "total_notas_brl": 0, "n_notas": 0})
agg["diferenca_brl"] = (agg["total_cartao_brl"] - agg["total_notas_brl"]).round(2)
agg = agg.rename(columns={"agg": "fornecedor"}).sort_values(["fornecedor", "competencia"])
nekt.save_table(df=agg, layer_name="Trusted", table_name="conciliacao_agregada", folder_name="Conciliacao", mode="overwrite")
print("OK")
