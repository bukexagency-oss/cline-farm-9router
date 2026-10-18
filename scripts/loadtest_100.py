#!/usr/bin/env python3
"""Sanitized helper from the Cline-farm / 9router work.
Credentials removed — supply your own via env or /tmp files as noted in README.
"""
"""FINAL: 100 parallel requests. Prove they spread across ~100 distinct connections + proxies."""
import json, os, sqlite3, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

MI = os.environ.get("MIBP_MI", "http://127.0.0.1:20128")
DB = os.environ.get("MIBP_DB", "/home/belfara/.9router/db/data.sqlite")
KEY = open(os.environ.get("MIBP_KEY_FILE", "/tmp/mibp_client_key.txt")).read().strip()
MODEL = os.environ.get("MIBP_MODEL", "cl/anthropic/claude-sonnet-4.6")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 100
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 20

con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
mark = max((r[0] or 0) for r in con.execute("SELECT id FROM usageHistory")) or 0
con.close()


def one(i):
    body = {"model": MODEL, "stream": False, "max_tokens": 6,
            "messages": [{"role": "user", "content": "Reply with OK%d" % i}]}
    r = urllib.request.Request(MI + "/v1/chat/completions", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            resp.read()
        return 200, round(time.time() - t0, 1)
    except urllib.error.HTTPError as e:
        return e.code, round(time.time() - t0, 1)
    except Exception:
        return 0, round(time.time() - t0, 1)


print("=== %d parallel requests (round-robin sticky=1, workers=%d) ===" % (N, WORKERS))
t0 = time.time()
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    res = list(ex.map(one, range(1, N + 1)))
wall = time.time() - t0
tally, times = {}, []
for s, dt in res:
    tally[s] = tally.get(s, 0) + 1
    times.append(dt)
times.sort()
print("tally=%s | wall=%.1fs | throughput=%.2f req/s" % (tally, wall, N / wall))
print("latency p50=%.1fs p95=%.1fs max=%.1fs" % (times[len(times) // 2], times[int(len(times) * .95) - 1], times[-1]))

time.sleep(6)
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
rows = con.execute("SELECT connectionId, status, COUNT(*) FROM usageHistory WHERE id > ? GROUP BY connectionId, status", (mark,)).fetchall()
agg = {}
for cid, st_, n in rows:
    nm = con.execute("SELECT name, data FROM providerConnections WHERE id=?", (cid,)).fetchone()
    if not nm:
        continue
    sid = "-"
    try:
        pp = json.loads(nm[1]).get("providerSpecificData", {})
        if pp.get("connectionProxyUrl") and "res-id-" in pp["connectionProxyUrl"]:
            sid = pp["connectionProxyUrl"].split("res-id-")[1].split(":")[0]
    except Exception:
        pass
    agg[nm[0]] = (n, sid, st_)
con.close()

print("\nRESULT: distinct connections=%d | distinct proxies=%d | served requests=%d"
      % (len(agg), len(set(v[1] for v in agg.values() if v[1] != "-")), sum(v[0] for v in agg.values())))
multi = {k: v for k, v in agg.items() if v[0] > 1}
print("connections serving >1 request: %d %s" % (len(multi), sorted(multi.items(), key=lambda x: -x[1][0])[:5]))
if agg:
    print("sample:", sorted(agg.items())[:3])
