# Deployment Notes — rebuild 100-key farm (17Okt26)

Catatan hasil rebuild farm 100 key di deployment 9router-mibp berjalan
(build `latest`, port :20128, DB `~/.9router/db/data.sqlite`). Baca ini
sebelum mengikuti langkah README di build yang sama.

## 1. Route API: `/api/provider-connections` TIDAK ADA

Di build berjalan sekarang, koneksi provider dikelola oleh:

- `GET  /api/providers` → `{connections: [...]}` (bukan `/api/provider-connections`)
- `POST /api/providers` (create)
- `PATCH/DELETE /api/providers/{id}` — PATCH balas `405` di jalur ini

Semua script di `scripts/` sudah mengikuti route baru ini.

## 2. Membuat koneksi cline-apikey: jalur API DITOLAK, insert SQLite jalan

`POST /api/providers` menolak `provider: "cline"` dengan `400 Invalid provider`.
Akar masalah: di compiled build, `AI_PROVIDERS.cline = {refreshable: true}`
**tanpa `authModes`** — route memvalidasi
`AI_PROVIDERS[c]?.authModes?.includes("apikey")` → `undefined`. Registry source
open-sse (`providers/registry/cline.js`) memang mencantumkan
`authModes: ["oauth","apikey"]`, tapi itu dipakai jalur eksekusi chat, bukan
validasi route dashboard.

Yang terbukti jalan: insert row langsung ke tabel `providerConnections`
(satu backup DB dulu). Server membaca DB per-request (hot-read) — row baru
terlihat di API **tanpa restart container**.

Shape row (kolom: `id, provider, authType, name, email, priority, isActive,
data, createdAt, updatedAt`):

```json
{
  "apiKey": "<secret dari POST https://api.cline.bot/api/v1/api-keys>",
  "lastError": null,
  "lastErrorAt": null,
  "providerSpecificData": {
    "connectionProxyEnabled": true,
    "connectionProxyUrl": "http://user:pass@host:port",
    "connectionNoProxy": "localhost,127.0.0.1"
  },
  "testStatus": "untested"
}
```

## 3. KNOWN BUG: round-robin cline tidak berputar

Round-robin menyebar hanya kalau state `lastUsedAt` / `consecutiveUseCount`
ter-update setelah tiap request (logika di compiled chunk: sticky sampai
`consecutiveUseCount < stickyRoundRobinLimit`, lalu sort ASC `lastUsedAt`).
**Update tersebut tidak pernah tersimpan** — tidak masuk kolom maupun JSON
`data`, tanpa error di log. Akibatnya sort selalu jatuh ke koneksi pertama.

Fakta-fakta terukur:

- Nama field di settings adalah `stickyRoundRobinLimit` (global, default 3) —
  **bukan** `stickyLimit`.
- Menambahkan kolom `lastUsedAt`/`consecutiveUseCount` via `ALTER TABLE`
  (backup dulu) TIDAK memperbaiki — tulisan tetap tidak masuk.
- Failover TETAP bekerja: matikan koneksi pertama → request otomatis geser ke
  koneksi berikutnya. Jadi farm tetap berguna sebagai pool failover saat kena
  rate limit, meski bukan load-balancer.
- Koneksi provider `tokenharbor` terlihat "menyebar" karena model-lock
  per-model (`modelLock_<model>` di JSON `data`), bukan karena round-robin —
  `lastUsedAt` mereka juga selalu null.

Kalau ada perubahan upstream yang memperbaiki penyimpanan state ini, hapus
catatan ini dan verifikasi ulang dengan loadtest (distribusi >1 koneksi).

## 4. Keamanan container: DNS `EAI_AGAIN`

`docker exec 9router-mibp node -e 'require("dns").lookup(...)'` gagal
`EAI_AGAIN` semua domain, dan TCP ke IP publik dari dalam konteks exec
`ENETUNREACH` — padahal request lewat proses next-server berjalan normal
(terverifikasi: koneksi established ke IP upstream selama request, isi
completion nyata). Jangan menilai egress dari hasil `docker exec`;
verifikasi lewat request sungguhan atau `ss` di host.
