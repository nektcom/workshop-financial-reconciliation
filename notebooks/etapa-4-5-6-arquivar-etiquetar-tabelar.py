"""
Etapa 4+5+6 (invoices) — arquivar -> etiquetar -> tabela final.
- 4: salva cada nota fiscal distinta no Drive em PASTA-MAE>MM.AAAA (idempotente; dedup por nome+tamanho).
- 5: aplica label "anexo salvo" (ou "anexo nao salvo" em falha) no email, MANTENDO NAO LIDO. Cria labels se faltarem.
- 6: grava Trusted.invoices (1 linha por nota fiscal) com link do arquivo.
Le fiscais de Trusted.invoices_classificadas.
NOVO: dedup por NUMERO da nota (mantendo o PDF); sem numero -> valor+data, marcado p/ revisar.

>>> Antes de rodar, troque todos os placeholders <ASSIM> pelos seus valores (ver README). <<<
"""
import re, json
import pandas as pd
import nekt, fsspec, requests
from concurrent.futures import ThreadPoolExecutor

log = nekt.get_logger()
WORKSHOP_FOLDER_ID = "<ID_DA_PASTA_MAE_NO_DRIVE>"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE = "https://www.googleapis.com/drive/v3/files"
DRIVE_UP = "https://www.googleapis.com/upload/drive/v3/files"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"

def secret(k):
    return nekt.load_secret(key=k)

def get_token():
    r = requests.post(TOKEN_URL, timeout=30, data={
        "client_id": secret("<SECRET_GOOGLE_CLIENT_ID>"),
        "client_secret": secret("<SECRET_GOOGLE_CLIENT_SECRET>"),
        "refresh_token": secret("<SECRET_GOOGLE_REFRESH_TOKEN>"),
        "grant_type": "refresh_token"})
    r.raise_for_status()
    return r.json()["access_token"]

TOKEN = get_token()
H = {"Authorization": f"Bearer {TOKEN}"}
HJ = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# ---------- normalizacao de nome: AAAA.MM.DD - Fornecedor - MOEDA VALOR ----------
def limpar(s):
    s = s or ""
    for ch in '/\\:*?"<>|':
        s = s.replace(ch, "_")
    return re.sub(r"\s+", " ", s).strip()

def norm_moeda(m):
    s = str(m or ""); u = s.upper()
    if "US" in u: return "USD"
    if "R$" in s or "BRL" in u or "REA" in u: return "BRL"
    return s.strip()

def norm_valor(v):
    s = re.sub(r"[^0-9.,]", "", str(v or ""))
    if not s: return ""
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return f"{float(s):.2f}"
    except Exception:
        return ""

def montar_nome(ia_data, email_date, fornecedor, moeda, valor, ext):
    d = str(ia_data or "")
    if not re.match(r"\d{4}-\d{2}-\d{2}", d):
        d = str(email_date or "")[:10]
    d = d.replace("-", ".")
    if not re.match(r"\d{4}\.\d{2}\.\d{2}", d):
        d = "0000.00.00"
    forn = limpar(fornecedor) or "Fornecedor"
    mo, va = norm_moeda(moeda), norm_valor(valor)
    val = f"{mo} {va}".strip() or "valor-a-confirmar"
    nome = f"{d} - {forn} - {val}.{ext}"
    sub = f"{d.split('.')[1]}.{d.split('.')[0]}" if d != "0000.00.00" else "sem-data"
    return nome, sub

# ---------- Drive (find/create pasta, list, upload) ----------
def find_folder(parent, name):
    q = f"'{parent}' in parents and name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    r = requests.get(DRIVE, headers=H, timeout=30, params={
        "q": q, "fields": "files(id,name)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    r.raise_for_status()
    fs = r.json().get("files", [])
    return fs[0]["id"] if fs else None

def create_folder(parent, name):
    r = requests.post(DRIVE, headers=HJ, timeout=30, params={"supportsAllDrives": "true"},
                      json={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent]})
    r.raise_for_status()
    return r.json()["id"]

def list_files(folder_id):
    out, page = {}, None
    while True:
        params = {"q": f"'{folder_id}' in parents and trashed=false",
                  "fields": "files(id,name,size,webViewLink),nextPageToken",
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "pageSize": 1000}
        if page: params["pageToken"] = page
        r = requests.get(DRIVE, headers=H, timeout=30, params=params); r.raise_for_status()
        j = r.json()
        for f in j.get("files", []):
            out[f["name"]] = f
        page = j.get("nextPageToken")
        if not page: break
    return out

def upload(folder_id, name, mime, data):
    b = "===boundary==="
    meta = {"name": name, "parents": [folder_id]}
    body = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
            f"--{b}\r\nContent-Type: {mime or 'application/octet-stream'}\r\n\r\n").encode("utf-8") \
        + data + f"\r\n--{b}--".encode("utf-8")
    r = requests.post(DRIVE_UP, timeout=180,
                      headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": f"multipart/related; boundary={b}"},
                      params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id,name,webViewLink"},
                      data=body)
    r.raise_for_status()
    return r.json()

# ---------- Gmail (labels) ----------
def get_labels():
    r = requests.get(f"{GMAIL}/labels", headers=H, timeout=30); r.raise_for_status()
    return {l["name"]: l["id"] for l in r.json().get("labels", [])}

def create_label(name):
    r = requests.post(f"{GMAIL}/labels", headers=HJ, timeout=30,
                      json={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"})
    r.raise_for_status()
    return r.json()["id"]

def add_label(msg_id, label_id):
    r = requests.post(f"{GMAIL}/messages/{msg_id}/modify", headers=HJ, timeout=30,
                      json={"addLabelIds": [label_id]})  # so adiciona; NAO mexe em UNREAD
    r.raise_for_status()

labels = get_labels()
LID_SALVO = labels.get("anexo salvo") or create_label("anexo salvo")
LID_NAO = labels.get("anexo não salvo") or create_label("anexo não salvo")

# ---------- dados: fiscais ----------
df = nekt.load_table(layer_name="Trusted", table_name="invoices_classificadas")
df = df[df["ia_is_fiscal"].astype(str).str.lower() == "true"].copy()
df["tamanho"] = df["tamanho_bytes"].fillna(0).astype("int64")
recs = []
for _, r in df.iterrows():
    nome, sub = montar_nome(r["ia_data"], r["email_date"], r["ia_fornecedor"], r["ia_moeda"], r["ia_valor"], r["ext"])
    recs.append({**r.to_dict(), "nome_novo": nome, "subpasta": sub})
fisc = pd.DataFrame(recs)

# ===== dedup mantendo o PDF: por NUMERO quando existe; senao por valor+data (marcado p/ revisar) =====
def _txt(s):
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()
def _num(s):
    s = re.sub(r"[^0-9a-z]", "", str(s or "").lower())
    return s.lstrip("0") or s  # tira zeros a esquerda
PREF_FMT = {"pdf": 0, "xml": 1, "png": 2, "jpg": 2, "jpeg": 2}  # menor = melhor formato
fisc["_pref"] = fisc["ext"].map(lambda e: PREF_FMT.get(str(e).lower(), 9))
fisc["_forn"] = fisc["ia_fornecedor"].map(_txt)
fisc["_num"] = fisc["ia_numero"].map(_num) if "ia_numero" in fisc.columns else ""
fisc["_val"] = fisc["ia_valor"].map(norm_valor)
fisc["_dat"] = fisc["ia_data"].astype(str).str[:10]
com_num = fisc[fisc["_num"] != ""].copy()
sem_num = fisc[fisc["_num"] == ""].copy()
# com numero: 1 por (fornecedor + numero), preferindo PDF -> junta a mesma nota em formatos/copias diferentes
com_num = com_num.sort_values("_pref").drop_duplicates(subset=["_forn", "_num"], keep="first")
com_num["dedup_status"] = "ok_por_numero"
# sem numero: dedup conservador por (fornecedor + valor + data), preferindo PDF, e MARCA p/ revisar
sem_num = sem_num.sort_values("_pref").drop_duplicates(subset=["_forn", "_val", "_dat"], keep="first")
sem_num["dedup_status"] = "sem_numero_revisar"
fisc = pd.concat([com_num, sem_num], ignore_index=True).drop(columns=["_pref", "_forn", "_num", "_val", "_dat"])
log.info("Apos dedup: %d por numero + %d sem numero (revisar)", len(com_num), len(sem_num))

# ---------- 4: pastas dos meses + upload distinto (nome+tamanho) ----------
subs = sorted(fisc["subpasta"].unique())
folder_ids, existing = {}, {}
for sub in subs:
    fid = find_folder(WORKSHOP_FOLDER_ID, sub) or create_folder(WORKSHOP_FOLDER_ID, sub)
    folder_ids[sub] = fid
    existing[sub] = list_files(fid)

distintos = fisc.drop_duplicates(subset=["subpasta", "nome_novo", "tamanho"])
link_por_chave, falhas_chave = {}, set()
for _, r in distintos.iterrows():
    chave = (r["subpasta"], r["nome_novo"], int(r["tamanho"]))
    fid = folder_ids[r["subpasta"]]
    ja = existing[r["subpasta"]].get(r["nome_novo"])
    try:
        if ja is not None and int(ja.get("size") or -1) == int(r["tamanho"]):
            link_por_chave[chave] = {"link": ja.get("webViewLink"), "status": "ja_existia"}
            continue
        nome_final = r["nome_novo"]
        if ja is not None:  # mesmo nome, tamanho diferente -> nota distinta: desambigua
            base, ext = nome_final.rsplit(".", 1)
            nome_final = f"{base} ~{int(r['tamanho'])}.{ext}"
        with fsspec.open(r["s3_path"], "rb") as fh:
            data = fh.read()
        up = upload(fid, nome_final, r["mime"], data)
        existing[r["subpasta"]][nome_final] = {"name": nome_final, "size": str(r["tamanho"]), "webViewLink": up.get("webViewLink")}
        link_por_chave[chave] = {"link": up.get("webViewLink"), "status": "novo", "nome_final": nome_final}
    except Exception as e:
        link_por_chave[chave] = {"link": None, "status": f"erro: {e!r}"}
        falhas_chave.add(chave)

# ---------- 5: etiquetar emails (mantendo NAO LIDO) ----------
msg_status = {}
for _, r in fisc.iterrows():
    chave = (r["subpasta"], r["nome_novo"], int(r["tamanho"]))
    ok = link_por_chave.get(chave, {}).get("link") is not None
    msg_status.setdefault(r["message_id"], True)
    if not ok:
        msg_status[r["message_id"]] = False

def do_label(item):
    msg, ok = item
    try:
        add_label(msg, LID_SALVO if ok else LID_NAO)
        return msg, ("salvo" if ok else "nao_salvo")
    except Exception as e:
        return msg, f"erro: {e!r}"

label_res = {}
with ThreadPoolExecutor(max_workers=6) as ex:
    for msg, res in ex.map(do_label, list(msg_status.items())):
        label_res[msg] = res

# ---------- 6: tabela final ----------
out = []
for _, r in fisc.iterrows():
    chave = (r["subpasta"], r["nome_novo"], int(r["tamanho"]))
    info = link_por_chave.get(chave, {})
    out.append({
        "message_id": r["message_id"], "arquivo_original": r["arquivo"],
        "nome_arquivo": info.get("nome_final", r["nome_novo"]), "tipo": r["ia_tipo"],
        "fornecedor": r["ia_fornecedor"], "numero": r.get("ia_numero"),
        "moeda": norm_moeda(r["ia_moeda"]), "valor": norm_valor(r["ia_valor"]),
        "data_doc": r["ia_data"], "subpasta": r["subpasta"], "drive_link": info.get("link"),
        "drive_status": info.get("status"), "dedup_status": r.get("dedup_status"),
        "label": label_res.get(r["message_id"]), "email_date": r["email_date"],
    })
res_df = pd.DataFrame(out)
nekt.save_table(df=res_df, layer_name="Trusted", table_name="invoices",
                folder_name="Conciliacao", mode="overwrite")
print("OK", len(res_df))
