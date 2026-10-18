#!/usr/bin/env python3
"""Sanitized helper from the Cline-farm / 9router work.
Credentials removed — supply your own via env or /tmp files as noted in README.
"""
"""Rollout: assign one sticky residential proxy per cline farm connection (1:1, 100 proxies)."""
import json, os, sqlite3, sys, urllib.error, urllib.request

MI = os.environ.get("MIBP_MI", "http://127.0.0.1:20128")
DB = os.environ.get("MIBP_DB", "/home/belfara/.9router/db/data.sqlite")
_CK = os.environ.get("MIBP_COOKIE_FILE", "/tmp/mibp_cookie.txt")
COOKIE = os.environ.get("MIBP_COOKIE") or (open(_CK).read().strip() if os.path.exists(_CK) else "")
PF = os.environ.get("MIBP_PROXIES_FILE", "/home/belfara/ai-stack/farm/cline-farm/proxies.txt")
BK = os.environ.get("MIBP_BACKUP_FILE", "/home/belfara/ai-stack/farm/cline-farm/backup-cline-state.json")
APPLY = "--apply" in sys.argv


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


proxies = [l.strip() for l in open(PF) if l.strip()]
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
farms = []
for cid, name, data in con.execute(
        "SELECT id, name, data FROM providerConnections WHERE provider='cline' AND name LIKE 'farm-%' ORDER BY name"):
    try:
        psd = json.loads(data).get("providerSpecificData", {})
    except Exception:
        psd = {}
    farms.append((cid, name, psd.get("connectionProxyEnabled"), psd.get("connectionProxyUrl", "")))
con.close()
print("farm connections: %d | proxies: %d" % (len(farms), len(proxies)))
already = sum(1 for f in farms if f[2])
print("already proxied: %d" % already)

plan = []
for i, (cid, name, en, url) in enumerate(farms):
    p = proxies[i % len(proxies)]
    sid = p.split("res-id-")[1].split(":")[0]
    plan.append((cid, name, p, sid))

print("\nsample plan (first 5):")
for cid, name, p, sid in plan[:5]:
    print("  %-10s -> %s (%s)" % (name, sid, p.split("@")[1]))

if not APPLY:
    print("\n(dry-run; pass --apply)")
    raise SystemExit(0)

ok = fail = 0
for cid, name, p, sid in plan:
    st, b = api("PUT", "/api/providers/%s" % cid, {
        "connectionProxyEnabled": True, "connectionProxyUrl": p,
        "connectionNoProxy": "", "strictProxy": True})
    if st == 200:
        ok += 1
    else:
        fail += 1
        print("  FAIL %s -> %s %s" % (name, st, b[:110]))
print("\napplied: ok=%d fail=%d" % (ok, fail))

# verify by reading DB
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
n = 0
sids = set()
for (data,) in con.execute("SELECT data FROM providerConnections WHERE provider='cline' AND name LIKE 'farm-%'"):
    d = json.loads(data)
    p = d.get("providerSpecificData", {})
    if p.get("connectionProxyEnabled") and p.get("connectionProxyUrl"):
        n += 1
        sids.add(p["connectionProxyUrl"].split("res-id-")[1].split(":")[0])
con.close()
print("VERIFY: farm conns with proxy enabled = %d | distinct proxies = %d" % (n, len(sids)))
