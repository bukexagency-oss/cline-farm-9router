#!/usr/bin/env python3
"""Sanitized helper from the Cline-farm / 9router work.
Credentials removed — supply your own via env or /tmp files as noted in README.
"""
"""Enable round-robin for provider cline, then measure spread across connections/IPs."""
import json, os, sqlite3, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

MI = os.environ.get("MIBP_MI", "http://127.0.0.1:20128")
DB = os.environ.get("MIBP_DB", "/home/belfara/.9router/db/data.sqlite")
_CK = os.environ.get("MIBP_COOKIE_FILE", "/tmp/mibp_cookie.txt")
COOKIE = os.environ.get("MIBP_COOKIE") or (open(_CK).read().strip() if os.path.exists(_CK) else "")
_KF = os.environ.get("MIBP_KEY_FILE", "/tmp/mibp_client_key.txt")
KEY = os.environ.get("MIBP_KEY") or (open(_KF).read().strip() if os.path.exists(_KF) else "")
MODEL = os.environ.get("MIBP_MODEL", "cl/anthropic/claude-sonnet-4.6")
STICKY = int(sys.argv[1]) if len(sys.argv) > 1 else 1
N = int(sys.argv[2]) if len(sys.argv) > 2 else 12


def api(method, path, payload=None, timeout=40):
    h = {"Content-Type": "application/json", "Cookie": COOKIE}
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(MI + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:
        return 0, str(e)[:120]


# --- 1) read current settings ---
st, body = api("GET", "/api/settings")
cur = json.loads(body) if st == 200 else {}
ps = cur.get("providerStrategies") or {}
print("before: providerStrategies=%s" % json.dumps(ps))

# --- 2) set round-robin for cline ---
nxt = dict(ps)
nxt["cline"] = {"fallbackStrategy": "round-robin", "stickyRoundRobinLimit": STICKY}
st, body = api("PATCH", "/api/settings", {"providerStrategies": nxt})
print("PATCH -> HTTP %s %s" % (st, body[:120]))

st, body = api("GET", "/api/settings")
after = (json.loads(body).get("providerStrategies") or {})
print("after : %s" % json.dumps(after))
if after.get("cline", {}).get("fallbackStrategy") != "round-robin":
    print("!! strategy not persisted — abort"); raise SystemExit(1)

# --- 3) baseline marker ---
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
mark = max((r[0] or 0) for r in con.execute("SELECT id FROM usageHistory")) or 0
con.close()

# --- 4) N parallel requests ---
def one(i):
    body = {"model": MODEL, "stream": False, "max_tokens": 6,
            "messages": [{"role": "user", "content": "Say OK%d" % i}]}
    r = urllib.request.Request(MI + "/v1/chat/completions", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(r, timeout=200) as resp:
            return resp.status, json.loads(resp.read().decode())["choices"][0]["message"]["content"][:10]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:50].replace("\n", " ")
    except Exception as e:
        return 0, str(e)[:40]


print("\n=== %d parallel requests (stickyLimit=%d) ===" % (N, STICKY))
t0 = time.time()
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(one, range(1, N + 1)))
tally = {}
for s, _ in res:
    tally[s] = tally.get(s, 0) + 1
print("tally=%s wall=%.1fs" % (tally, time.time() - t0))

# --- 5) who served ---
time.sleep(3)
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
rows = con.execute("SELECT connectionId, status, COUNT(*) FROM usageHistory WHERE id > ? GROUP BY connectionId, status", (mark,)).fetchall()
print("\nserved by:")
agg = {}
for cid, st_, n in rows:
    nm = con.execute("SELECT name, data FROM providerConnections WHERE id=?", (cid,)).fetchone()
    sid = "-"
    if nm:
        try:
            pp = json.loads(nm[1]).get("providerSpecificData", {})
            if pp.get("connectionProxyUrl") and "res-id-" in pp["connectionProxyUrl"]:
                sid = pp["connectionProxyUrl"].split("res-id-")[1].split(":")[0]
        except Exception:
            pass
    print("  %-10s n=%-3d %-6s proxy=%s" % (nm[0] if nm else "?", n, st_, sid))
    if nm:
        agg[nm[0]] = sid
con.close()
print("\nRESULT: distinct connections=%d | distinct proxies=%d" % (len(agg), len(set(agg.values()))))
