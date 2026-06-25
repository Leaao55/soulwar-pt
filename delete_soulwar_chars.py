"""
Deleta do Soulwar PT Manager os personagens com Soul War completa.
Faz parse inteligente do estado atual (suporta duplo encode do run anterior)
e re-salva no formato correto: objetos diretos, body = JSON.stringify(payload).
"""
import requests, json, sys

SHEETS_API_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbwBhHkqkgtJoWDU-R6ge89TBpmBkLeBhLTdrsos3xVWonY-xJP8pIhgHt7YPfbTy2fy/exec"
)

with open("soulwar_results.json", encoding="utf-8") as f:
    to_delete_names = {c["name"].strip().lower() for c in json.load(f)}

print(f"Personagens a deletar: {len(to_delete_names)}")

# --- 1. Carrega estado atual ---
resp = requests.get(SHEETS_API_URL, params={"action": "load", "secret": "rbn_x9k2_leao_2026_soulwar"}, timeout=30)
resp.raise_for_status()
payload = resp.json()

if not payload.get("ok"):
    print("ERRO:", payload)
    sys.exit(1)

data = payload["data"]

def parse(field, default):
    """Faz parse do campo, desencapando duplo encode se necessário."""
    v = data.get(field, default)
    if isinstance(v, str) and v:
        v = json.loads(v)
    if isinstance(v, str) and v:  # duplo encode
        v = json.loads(v)
    return v if v is not None else default

chars               = parse("chars", [])
pts                 = parse("pts", [])
servers             = parse("servers", [])
members_raw         = parse("members", [])
next_id             = str(data.get("nextId", "1"))
rejected_ids        = parse("rejectedIds", [])
deleted_ids         = parse("deletedIds", [])
deleted_user_ids    = parse("deletedUserIds", [])
deleted_user_names  = parse("deletedUserNames", [])
backup_chars        = parse("backup_chars", [])
ranking_weekly      = parse("ranking_weekly", [])
ranking_alltime     = parse("ranking_alltime", [])
pt_observations     = parse("pt_observations", {})

print(f"Total chars no banco: {len(chars)}")

# --- 2. Identifica e remove chars com Soul War ---
ids_to_delete = []
for c in chars:
    if c.get("name", "").strip().lower() in to_delete_names:
        ids_to_delete.append(int(c["id"]))
        print(f"  Deletando: {c['name']} (id={c['id']})")

missing = to_delete_names - {c.get("name", "").strip().lower() for c in chars}
for n in missing:
    print(f"  INFO: '{n}' ja nao esta no banco (deletado anteriormente)")

id_set = set(ids_to_delete)
if id_set:
    chars_before = len(chars)
    chars = [c for c in chars if int(c["id"]) not in id_set]
    print(f"Chars removidos agora: {chars_before - len(chars)}")

    deleted_ids_set = set(int(x) for x in deleted_ids)
    deleted_ids_set.update(ids_to_delete)
    deleted_ids = list(deleted_ids_set)

    for pi, pt in enumerate(pts):
        pts[pi] = [None if (x is not None and int(x) in id_set) else x for x in pt]
else:
    print("Chars ja deletados — re-salvando apenas para corrigir o formato.")

# --- 3. Reconstroi members com bloco __USERS__ ---
TAG = "__USERS__:"
users = []
user_next_id = 1
extras = []
for m in members_raw:
    if isinstance(m, str) and m.startswith(TAG):
        try:
            blob = json.loads(m[len(TAG):])
            users = blob.get("u", [])
            user_next_id = blob.get("n", 1)
        except Exception:
            pass
    else:
        extras.append(m)

deleted_user_names_set = set(deleted_user_names)
approved_names = {u["name"] for u in users if u.get("status") == "approved"}
extras_clean = [m for m in extras if m not in approved_names and m not in deleted_user_names_set]
members_payload = [*extras_clean, TAG + json.dumps({"u": users, "n": user_next_id})]

# --- 4. Payload CORRETO: objetos diretos, sem json.dumps nos valores ---
save_payload = {
    "secret":           "rbn_x9k2_leao_2026_soulwar",
    "chars":            chars,
    "pts":              pts,
    "servers":          servers,
    "members":          members_payload,
    "nextId":           next_id,
    "rejectedIds":      rejected_ids,
    "deletedIds":       deleted_ids,
    "deletedUserIds":   deleted_user_ids,
    "deletedUserNames": deleted_user_names,
    "backup_chars":     backup_chars,
    "ranking_weekly":   ranking_weekly,
    "ranking_alltime":  ranking_alltime,
    "pt_observations":  pt_observations,
}

print(f"Salvando com {len(chars)} chars...")
save_resp = requests.post(
    SHEETS_API_URL,
    headers={"Content-Type": "text/plain"},
    data=json.dumps(save_payload),
    timeout=60,
)
save_resp.raise_for_status()
save_json = save_resp.json()
print("Resposta:", save_json)

if save_json.get("ok"):
    print(f"OK: estado salvo corretamente. {len(ids_to_delete)} char(s) deletado(s).")
else:
    print("ERRO:", save_json)
    sys.exit(1)
