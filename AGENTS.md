# Agent instructions for MATEbot

You are likely here because a human asked their AI assistant to help with
MATEbot — either to **set it up** for their GaggiMate espresso machine or to
**work on the code**. Both playbooks follow.

## What this is

MATEbot watches a GaggiMate machine over its local WebSocket API, messages the
user after every shot (Telegram or Discord) to log rating/beans/grind/doses,
writes those notes back into the machine's own shot history, and optionally
archives everything to a git repo with a generated shot-journal site.

## Playbook: helping someone set it up

Adapt to the user's skill level — ask what they have before prescribing.
The decision tree:

1. **Where will it run?** It needs any always-on device on the same network
   as the machine: a Raspberry Pi, NAS, home server, old laptop. No port
   forwarding, no domain — outbound internet only.
2. **Pick an install path:**
   - Most users → **Docker**: copy `docker-compose.example.yml`, fill in env
     vars, `docker compose up -d`. Image: `ghcr.io/alexnly/matebot:latest`
     (multi-arch, Raspberry Pi works).
   - Python users → `pipx install "matebot[telegram]"` (or plain pip),
     then `matebot run` (systemd unit or `screen` for persistence).
   - NixOS users → flake input + `services.matebot` module (see README).
3. **Telegram bot** (the fiddly part for novices — walk them through it):
   1. In Telegram, talk to `@BotFather` → `/newbot` → pick a name → copy the
      token (looks like `123456:ABC-...`). Never paste tokens into git.
   2. Have the user send their new bot any message.
   3. Get the chat id: `https://api.telegram.org/bot<TOKEN>/getUpdates` →
      `result[0].message.chat.id`.
   4. `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` go into the environment
      (compose file, EnvironmentFile, or shell).
4. **Machine address**: set `MATEBOT_MACHINE_HOST`. Prefer the machine's IP
   over `gaggimate.local` (mDNS is flaky on some networks) and recommend a
   static DHCP lease in the router.
5. **Verify**: start the bot, run a ≥10 s brew-mode shot (an empty blind
   basket works; water/steam modes do not trigger). The questionnaire should
   arrive within seconds of the shot ending.
6. **Optional extras**, in ascending effort: journal repo + GitHub Pages
   (README "Publishing your journal"), dial-in hints (on by default), bean
   bag tracking (`/newbag`), smart plug hooks for cold-start `/wake`
   (README "Smart plug cold start").

Common failure modes: token/chat id swapped or quoted wrong; bot and machine
on different networks/VLANs; machine powered off at a dumb power strip;
shots shorter than `MATEBOT_MIN_SHOT_S` (default 10 s) being ignored;
firmware older than v1.7 (no binary shot logs — unsupported).

## Playbook: working on the code

- Layout: `src/matebot/` — `machine.py` (WS/HTTP client), `watcher.py` (shot
  detection), `conversation.py` (questionnaire engine, messenger-agnostic),
  `messengers/` (Telegram/Discord backends), `commands.py` (slash commands),
  `hints.py`, `bags.py`, `digest.py` (features), `sync.py` (git archive),
  `sitegen.py` + `web/` (journal site), `plot.py` (Telegram PNG chart),
  `slog.py` (binary format decoder — start here to understand the data).
- Dev loop: `pip install -e ".[all,dev]"` then `pytest -q` and
  `ruff check src tests`. Or `nix develop`.
- Contribution flow: branch → PR → CI must pass (branch protection, no direct
  pushes to `main`) → squash merge. Keep commits/PRs single-purpose.
- Tests are required for behavior changes. The conversation engine is tested
  against a `FakeMessenger`; the watcher against a recorded frame fixture
  (`tests/fixtures/status_frames.jsonl`); the decoder against a real shot
  (`tests/fixtures/000004.slog` — golden values in `test_slog.py`).

### GaggiMate firmware gotchas (hard-won; do not rediscover these)

- `req:history:notes:save`: the request-level `id` becomes the notes filename
  verbatim. Older firmware reads the zero-padded name (`000059.json`),
  current nightlies the unpadded one (`59.json`) — MATEbot saves under BOTH.
  `rating` must be a number, every other field a string.
- `req:change-mode` gets **no response** — never wait for one (fire-and-forget
  via `send_event`; confirmation comes from the status stream).
- Missing files under `/api/history/` return **HTTP 200 with gzipped SPA
  index.html**, not 404 — validate content (`SHOT` magic / leading `{`) on
  every fetch.
- The ESP32 drops WebSocket clients whenever its send queue fills (e.g. the
  web UI is open). Reconnect loops must be bulletproof; nothing may
  crash-loop on a dropped socket.
- New-shot ids come from polling `/api/history/index.bin` (`flags & 1`,
  `id > last_known`) — never from `req:history:list` (heavy on the ESP32)
  and never from timestamps (`startEpoch` is wrong without NTP).
- Shot files are finalized up to ~1 min after the pump stops (bluetooth
  scale settle time).

### License boundary (important)

GaggiMate's own source (including its web UI) is **CC BY-NC-SA** — it must
never be copied into this MIT repo. Matching its *look* (colors, layout,
axis ranges) via independent implementation is fine and is what
`web/app.js` and `plot.py` do.

### Secrets

Tokens live in the environment only. `settings.json` written to data repos
must keep `wifiSsid`, `wifiPassword`, `apPassword`, `haPassword` redacted
(`machine.py` does this — keep it that way).
