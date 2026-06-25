"""
Etapa 10 — Tabelas de APRESENTACAO para o destination Google Sheets (3 abas).
Gera Trusted.saida_invoices, saida_transacoes, saida_resumo — ja organizadas
(coluna 'ordem' p/ ordenacao na planilha, status, anotacao da IA).

>>> Antes de rodar, troque todos os placeholders <ASSIM> pelos seus valores (ver README). <<<
"""
import pandas as pd
import nekt

log = nekt.get_logger()

def canon_agg(name):  # mesmos fornecedores recorrentes da Etapa 8+9
    s = str(name or "").lower()
    if "facebk" in s or "facebook" in s:
        return "Meta/Facebook"
    if "google ads" in s or "google brasil" in s:
        return "Google Ads"
    return None

def fmt(v):
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return ""

conc = nekt.load_table(layer_name="Trusted", table_name="conciliacao")
inv = nekt.load_table(layer_name="Trusted", table_name="invoices").drop_duplicates(subset=["drive_link"]).copy()
cc = nekt.load_table(layer_name="Trusted", table_name="transacoes_cartao")
cagg = nekt.load_table(layer_name="Trusted", table_name="conciliacao_agregada")

comp = conc[conc["tipo_registro"] == "compra"].copy()
matched = comp[comp["status"] == "conciliado"]
link2card = {r["nota_link"]: r for _, r in matched.iterrows() if r.get("nota_link")}
matched_links = set(link2card.keys())

STATUS_LABEL = {"conciliado": "Conciliada", "agregado": "Agregada", "nao_conciliado": "Nao conciliada"}
PRIO = {"Conciliada": 1, "Agregada": 2, "Nao conciliada": 3, "N/A": 4}

# ===== TRANSACOES CARTAO =====
def anot_compra(r):
    if r["status"] == "conciliado":
        d = int(r["dias_diff"]) if pd.notna(r["dias_diff"]) else "?"
        return (f"Conciliada com nota '{r['nota_fornecedor']}' ({r['moeda']} {fmt(r['nota_valor'])}, {r['nota_data']}) "
                f"- regra: {r['regra_match']}; {d} dia(s) de diferenca.")
    if r["status"] == "agregado":
        return f"Conciliacao AGREGADA ({r['fornecedor_agregado']}) - ver aba Resumo (por competencia)."
    if str(r.get("parcela") or "").strip():
        return f"Nao conciliada - compra PARCELADA ({r['parcela']}); a nota e da compra integral."
    return "Nao conciliada - sem nota nas faturas de <MES/ANO> (cobranca de outro mes, pago fora do cartao, ou sem nota por anexo)."

linhas = []
for _, r in comp.iterrows():
    st = STATUS_LABEL.get(r["status"], r["status"])
    linhas.append({"status": st, "banco": r["banco"], "data": r["data_cartao"],
                   "estabelecimento": r["estabelecimento"], "portador": r["portador"],
                   "cartao_final": r["cartao_final"], "moeda": r["moeda"],
                   "valor_moeda": r["valor"], "valor_brl": r["valor_brl"], "parcela": r["parcela"],
                   "anotacao_ia": anot_compra(r), "nota_fornecedor": r["nota_fornecedor"], "nota_link": r["nota_link"]})
for _, r in cc[cc["tipo"].isin(["pagamento", "imposto"])].iterrows():
    linhas.append({"status": "N/A", "banco": r["banco"], "data": r["data_transacao"],
                   "estabelecimento": r["estabelecimento"], "portador": r["portador"],
                   "cartao_final": r["cartao_final"], "moeda": r["moeda_origem"],
                   "valor_moeda": r["valor_origem"], "valor_brl": r["valor_brl"], "parcela": r["parcela"],
                   "anotacao_ia": f"Nao aplicavel a conciliacao ({r['tipo']}).", "nota_fornecedor": None, "nota_link": None})
t = pd.DataFrame(linhas)
t["_p"] = t["status"].map(PRIO).fillna(9)
t["_v"] = pd.to_numeric(t["valor_brl"], errors="coerce").fillna(0).abs()
t = t.sort_values(["_p", "_v"], ascending=[True, False]).reset_index(drop=True)
t.insert(0, "ordem", range(1, len(t) + 1))
t = t.drop(columns=["_p", "_v"])
nekt.save_table(df=t, layer_name="Trusted", table_name="saida_transacoes", folder_name="Saida", mode="overwrite")

# ===== INVOICES =====
def status_inv(r):
    if r.get("drive_link") in matched_links:
        return "Conciliada"
    if canon_agg(r["fornecedor"]):
        return "Agregada"
    return "Nao conciliada"

def anot_inv(r):
    link = r.get("drive_link")
    if link in matched_links:
        c = link2card[link]
        return f"Conciliada com a cobranca '{c['estabelecimento']}' (R$ {fmt(c['valor_brl'])}, {c['data_cartao']}) no cartao {c['banco']}."
    if canon_agg(r["fornecedor"]):
        return f"Conciliacao AGREGADA ({canon_agg(r['fornecedor'])}) - ver aba Resumo."
    return "Nao conciliada - sem cobranca nas faturas de <MES/ANO> (pago por transferencia/PJ, outro mes, ou documento duplicado invoice+recibo)."

li = []
for _, r in inv.iterrows():
    li.append({"status": status_inv(r), "tipo": r["tipo"], "fornecedor": r["fornecedor"],
               "data_documento": r["data_doc"], "competencia": str(r["data_doc"])[:7],
               "moeda": r["moeda"], "valor": r["valor"], "anotacao_ia": anot_inv(r), "arquivo_drive": r["drive_link"]})
iv = pd.DataFrame(li)
iv["_p"] = iv["status"].map(PRIO).fillna(9)
iv["_v"] = pd.to_numeric(iv["valor"], errors="coerce").fillna(0)
iv = iv.sort_values(["_p", "_v"], ascending=[True, False]).reset_index(drop=True)
iv.insert(0, "ordem", range(1, len(iv) + 1))
iv = iv.drop(columns=["_p", "_v"])
nekt.save_table(df=iv, layer_name="Trusted", table_name="saida_invoices", folder_name="Saida", mode="overwrite")

# ===== RESUMO GERENCIAL =====
def soma(cond):
    return round(pd.to_numeric(comp[cond]["valor_brl"], errors="coerce").sum(), 2)

rows = [
    ("Periodo das faturas de cartao", "<MES/ANO> (<BANCO> <periodo da fatura>; venc. <data>)"),
    ("CARTAO - compras (total)", str(len(comp))),
    ("  Conciliadas (1:1)", str(int((comp["status"] == "conciliado").sum()))),
    ("  Agregadas (recorrentes)", str(int((comp["status"] == "agregado").sum()))),
    ("  Nao conciliadas", str(int((comp["status"] == "nao_conciliado").sum()))),
    ("Total compras (R$)", fmt(soma(comp["status"].notna()))),
    ("  Conciliado (R$)", fmt(soma(comp["status"] == "conciliado"))),
    ("  Agregado (R$)", fmt(soma(comp["status"] == "agregado"))),
    ("  Nao conciliado (R$)", fmt(soma(comp["status"] == "nao_conciliado"))),
    ("INVOICES - distintas capturadas", str(len(inv))),
    ("  Conciliadas", str(int(inv["drive_link"].isin(matched_links).sum()))),
    ("  Agregadas (recorrentes)", str(int(inv["fornecedor"].apply(lambda x: canon_agg(x) is not None).sum()))),
    ("  Nao conciliadas (controle)", str(int((~inv["drive_link"].isin(matched_links) & inv["fornecedor"].apply(lambda x: canon_agg(x) is None)).sum()))),
    ("CONCILIACAO AGREGADA (recorrentes) por competencia", ""),
]
for _, r in cagg.iterrows():
    rows.append((f"  {r['fornecedor']} {r['competencia']}",
                 f"cartao R$ {fmt(r['total_cartao_brl'])} | notas R$ {fmt(r['total_notas_brl'])} | dif R$ {fmt(r['diferenca_brl'])}"))
rs = pd.DataFrame(rows, columns=["indicador", "valor"])
rs.insert(0, "ordem", range(1, len(rs) + 1))
nekt.save_table(df=rs, layer_name="Trusted", table_name="saida_resumo", folder_name="Saida", mode="overwrite")
print("OK")
