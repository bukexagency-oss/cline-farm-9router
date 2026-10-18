# Cline key farm on 9router (mibp fork)

Field notes from running **100 minted Cline API keys** through a self-hosted
[9router](https://github.com/decolua/9router) instance (mibp fork, v1.0.18),
each key egressing through its **own sticky residential proxy**, with
**round-robin** connection selection and **strictProxy** enforced.

Everything here was measured on a real deployment, not estimated.

## What's in this repo

```
docs/
  01-limit-audit.md            # is there a key cap / rate limit? (measured)
  02-proxy-rollout.md          # 1:1 proxy per connection
  03-strict-proxy-bugfix.md    # BUG: strictProxy dropped on legacy path
  04-round-robin-rotation.md   # fill-first -> round-robin
  05-full-100-proxy.md         # 102/102 provisioned + 100-parallel proof
patches/
  0001-strict-proxy-legacy-connections.patch   # the upstream-worthy fix
UPGRADE-AND-COMPAT.md         # old-repo problems -> fix, so a fresh install works
DEPLOYMENT-NOTES.md           # rebuild 100-key farm (17Okt26): route changes + round-robin bug
scripts/
  proxy_rollout.py             # assign one proxy per connection (idempotent)
  proxy_strict_all.py          # enable strictProxy on every proxied connection
  rotation_enable.py           # switch a provider to round-robin + measure spread
  loadtest_100.py               # N parallel requests, reports spread across conns/IPs
```

## Headline results

| Metric | Result |
|---|---|
| API keys minted / wired | **100** (`farm-001` … `farm-100`) |
| Key cap per account | **none** (minted past 38 on one account) |
| Rate limit observed | **none** — 127 sequential + 100 parallel calls, **0× HTTP 429** |
| Cost | **$0** (BYOK; 9router records notional $0.085/3.35M tok) |
| Connections with own proxy | **102 / 102** |
| `strictProxy` enforced | **102 / 102** |
| 100 parallel requests | **100/100 HTTP 200 · 100 distinct connections · 100 distinct proxies** |
| Throughput / latency | 2.72 req/s · p50 5.0s · p95 10.9s |
| Direct-IP leaks during load | **0** (only after fix — see [compat notes](UPGRADE-AND-COMPAT.md)) |

## ⚠️ Does a fresh install work? (read this first)

**On the old mibp build (v1.0.18): no — not for per-connection proxies.** A failing
proxy silently falls back to the **server's own IP** unless this fix is applied.
Everything else (keys, proxy wiring, rotation) works, but the leak is invisible
until you read the logs.

Three ways to make a new install behave correctly:

| Route | Action | Survives image rebuild? |
|---|---|---|
| **A. Patch the code** | `git apply patches/0001-…patch` **+ patch all compiled chunks + restart** | ❌ re-apply each build |
| **B. Use a proxy pool** (recommended) | Create the proxy as a **pool** with `strictProxy: true`; the pool branch honours it natively — **no patching** | ✅ |
| **C. Keep the leak** | Do nothing | ❌ requests escape to your server IP on proxy failure |

Full step-by-step, minified-chunk instructions, verification commands and a table
of **all six incompatibilities** we hit on the old build:
**[`UPGRADE-AND-COMPAT.md`](UPGRADE-AND-COMPAT.md)**

Quick self-check on any install — this count must be **0**:

```bash
docker logs --since 10m <container> | grep -c "falling back to direct"
```

## The bug worth upstreaming

`resolveConnectionProxyConfig()` (legacy per-connection branch) returned:

```js
strictProxy: poolStrictProxy,   // always false when no proxy pool is set
```

so the `strictProxy` flag stored on the connection was **discarded**. A failing
per-connection proxy therefore fell back to the **server's own IP**:

```
[ProxyFetch] Proxy failed, falling back to direct: fetch failed
```

That silently defeats the entire purpose of per-connection residential proxies.
The fix returns the connection's own flag:

```js
strictProxy: providerSpecificData?.strictProxy === true || poolStrictProxy,
```

After the fix, a dead proxy produces:

```
[502] [ProxyFetch] Proxy required but failed (strictProxy=true): fetch failed
[FALLBACK] <conn> UNAVAILABLE (502) -> NEXT ACCOUNT
```

i.e. it **fails closed** and rotates to another connection (another exit IP)
instead of leaking.

Apply with:

```bash
git apply patches/0001-strict-proxy-legacy-connections.patch
```

> Note: in a built Next.js deployment the module is duplicated across several
> `.next/server/chunks/*.js` files — patch **all** copies or the fix is inert.

## Configuration reference (no UI needed)

```bash
# 1) one proxy per connection
PUT /api/providers/{connectionId}
{ "providerSpecificData": {
    "connectionProxyEnabled": true,
    "connectionProxyUrl": "http://user:pass@host:port",
    "connectionNoProxy": [],
    "strictProxy": true } }

# 2) rotate connections instead of exhausting the first one
PATCH /api/settings
{ "providerStrategies": { "cline": {
    "fallbackStrategy": "round-robin",
    "stickyRoundRobinLimit": 1 } } }
```

Gotchas learned the hard way:

- The runtime key is **`providerStrategies`** — `providerOverrides` is a *different*
  setting that only carries header overrides.
- `strictProxy` is **not** read as a top-level field by the PUT handler; it must
  travel inside `providerSpecificData` (which is merged, so other fields survive).
- `stickyRoundRobinLimit: 1` = new connection per request (maximum spread).

## Caveats / honest limits

- Round-robin picks connections **without pre-checking per-model locks**; if many
  connections are locked for one model the router degrades to chained failover
  (correct, just slower).
- Round-robin spreads *requests*, not *capacity*: keys sharing one account still
  share that account's quota.
- Residential proxies bill **per GB** — 100 rotating IPs burn quota much faster.
- Compiled-chunk patches are lost on image rebuild; re-apply from `patches/`.

## License / ethics

The measurements and the bug analysis are ours and published for the operator
community. Scripts are provided as-is for systems **you own and control**.
Do not point them at accounts or infrastructure you are not authorised to use.
