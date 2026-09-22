# Privacy design

> English translation of [`PRIVACY.md`](PRIVACY.md). The Chinese original is authoritative.

This kind of tool touches **notifications, screen time, health, calendar** — the most sensitive
category of data. So the rules are fixed up front:

## Three hard rules

**1. Raw data only ever lives on your own server.**
No third party is involved. The hub is a single Python file plus SQLite; you can inspect
everything with `sqlite3 hub.db` at any time.

**2. Everything that reaches the model is de-identified first.**
`hub.py` has a **mandatory gate** that any model-facing context must pass:

| Raw | After de-identification |
|---|---|
| Notification body | numbers + hour-level timestamp only ("14h") |
| Calendar title ("dentist at the campus clinic") | type only ("other") |
| App name (Bilibili) | category only ("short video") |
| Course / teacher / room | "period N + class" only |
| Sleep 327 minutes | 330 minutes (half-hour granularity) |
| Device name | "watch / phone / PC" |
| To-do contents | count only |
| Process name / window title (PC side) | **never sent**; only "how many times you switched windows" |
| Disk / memory (PC side) | percentages only — no paths, no filenames |
| Baseline comparison ("42% more than usual") | **category name + percentage** only; no app names, no history series, no dates |

**You can check it:** `GET /llm-preview` returns "what the model would see this round" *and*
"what the model never sees". Look before you allow.

**2.5 Your own devices see the raw values.**
The `care` block in `/today` (used by the widget / web UI) is **not blurred** — battery is
34%, the song is the song, disk is 12% free. Reason: that is **your data on your device**,
reachable only with the same token; it and "the copy given to the model" are **two different
paths**. The model's path always goes through the table above. To compare:
`GET /llm-preview` for the model's copy, `GET /today` for yours.

**3. Keys never go to the server.**
The hub only has its own `token`; the **model API key stays on the machine that runs the
speaker layer**. The hub cannot even reach a model — it only produces structured facts.

## Transport and storage

- **Database encryption**: SQLite is not encrypted (whoever gets the file gets the whole
  history) → use `hubctl backup --encrypt`, or full-disk encryption:
  ```bash
  gocryptfs ~/whale-enc /root/hub     # directory-level encryption (example)
  # or attach a LUKS data disk to your cloud host and put /root/hub on it
  ```
- **Retention**: `privacy.retention_days` (default 365) prunes automatically; `0` keeps forever
- **Transport**: HTTPS + **certificate pinning** (the app trusts only your certificate);
  the private key is generated on your own server, `chmod 600`, **never leaves it**
- **Offline queue**: Android Keystore AES-256-GCM; if the key is unavailable it **degrades to
  plaintext rather than losing data** (security must not cost you data)
- **Cleartext**: `network_security_config.xml` sets `cleartextTrafficPermitted="false"`

### A **new** outbound path: direct delivery (since v0.1.17)

Besides the main "speaker → gateway → WeChat" route, the hub can send directly (`POST /push`):
① WeCom (WeChat Work) group bot ② a generic webhook (any endpoint accepting
`POST {"text": ...}`). **It is a path for data to leave the machine, so the rules are strict:**

- **Empty by default**: with `channels` unconfigured there is no such exit
  (`GET /channels` explicitly answers "empty").
- **Never automatic**: it is not wired into the decision loop; only an explicit
  `POST /push` sends (this avoids double-pushing with the speaker).
- **Say who receives it**: via WeCom = content goes to Tencent's servers; via generic
  webhook = goes to **your own** service. Neither goes through any server of this project.
- **The URL itself is the credential** (webhook URLs embed a key) → the status endpoint only
  answers "configured / empty" and **never prints the URL**.
- Sends are audited (`channel_send`: "sent one message of N chars", never the content).

## If you are the recipient

- **Never expose the hub to the open internet**: token + firewall restricted to your own IP + HTTPS
- The collector is a **private personal app**: don't publish it (accessibility /
  notification-listener permissions wouldn't be accepted anyway)
- Want others to use it? Let each person deploy their own — do not run a "shared hub"

## Distribution and hardening (added after self-testing)

**The distribution server only serves a whitelisted directory.** The small service used to
hand out APKs/scripts **must not expose an entire working directory** — it may contain scripts
with plaintext passwords, config files, other projects' sources; anyone who guesses a filename
can download it. **Expose only a `pub/` whitelist directory:**

```
curl http://.../hub.py            # ✓ allowed
curl http://.../hub.json          # 404 (config is not distributed)
curl http://.../devpw.sh          # 404 (scripts containing passwords are not distributed)
```

**Hub hardening** (three items added after security self-testing):
1. **Header-only `X-Token`** — `?token=` is no longer accepted (query tokens end up in server
   logs, browser history and proxy records)
2. **1 MB body limit** — oversized payloads are rejected (one big request could otherwise eat memory)
3. **240 requests/minute per source** — "good enough" rate limiting against abuse

## Added after an external critique (point by point)

| Criticism | Valid? | Action |
|---|---|---|
| Relay does not verify certificates by default | ✅ valid | Now refuses to start without a CA; `WHALE_INSECURE=1` only for debugging |
| Raw plaintext was persisted | ✅ valid | `privacy.store_raw_text` defaults to **false**: health notification bodies are no longer stored |
| Firewall not tightened | ✅ valid | Cleartext port removed from the public internet; HTTPS only; VPN/Tailscale recommended |
| Plaintext SQLite — steal the file, get everything | ✅ valid | `backup/dump --encrypt` (AES-256) provided; full-disk encryption recommended |
| A tampered collector can report anything | ✅ valid (not fully fixable) | Application-layer de-identification only guarantees the model-facing copy |
| MCU link had no retry/checksum | ✅ valid | Protocol gained `s=`/`c=`; relay persists and re-sends |
| A single file will be hard to maintain | ✅ valid | Deliberate (deploy = copy one file); may be split later |
| Can't take high concurrency / no queue | ⚠️ overstated | Personal scale is a few hundred requests a day; the limiter is 240/min — three orders of magnitude of headroom |
| SQLite can't hold years of time series | ⚠️ overstated | ~1000 rows/day ≈ 350k rows/year; SQLite handles millions fine |

## Self-test coverage

Auth (no/wrong token), parameter abuse (oversized limit), SQL injection, path traversal,
large payloads, method abuse and stack leakage, TLS pinning, webhook signature and replay,
distribution-directory traversal, direct delivery (stub server verifying the payload verbatim).
Result: auth / injection / traversal / replay / TLS all pass; the distribution leak and the
missing rate limit were **two real holes at the time, both fixed**.

**Latest re-test (22 checks)**: found one more case of **error messages leaking a server path**
(a 401 revealed the absolute path of `hub.json` to unauthenticated callers) — now a neutral
message. Also confirmed: >1 MB requests return `413`, SQL inside a metric name is handled
safely, all 10 path-traversal variants against the distribution endpoint return `404`, and the
query token for `/api/mcu` only works for that endpoint.
