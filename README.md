# Playtomic Tennis Court Monitor

Monitors court availability on Playtomic for your club and sends a **Telegram** alert when a
slot opens up in the windows you care about. Runs for free on **GitHub Actions** every ~30
minutes. Zero Python dependencies (standard library only, Python ≥ 3.9).

> Full documentation (Italian): [README.it.md](README.it.md)

## How it works

1. Polls the club's availability for the next days (public view, ~3 days ahead)
2. Filters slots by your windows, courts and durations (`config.json`)
3. Groups free slots **day → start time** and sends one compact Telegram message with a
   direct booking link per day
4. Remembers what it already notified (`state.json`) so you never get the same slot twice

With a **member session** the same endpoint returns ~10 days: an optional step renews the
session token in a headless Chromium at each run (see README.it.md §4).

## Sample alert

```
🎾 My Tennis Club
9 free slots · 3 days

📅 Monday 27/07 · book
    18:30 · 60 min · Court 1, Court 2 (clay)
    19:00 · 60 min · Court 5 (clay)

📅 Tuesday 28/07 · book
    09:00 · 60 min · Court 7, Court 8 (clay)
```

## Setup (once)

1. **Telegram**: create a bot with [@BotFather](https://t.me/BotFather), grab the token, message
   the bot, then read your `chat_id` from `getUpdates` (exact commands in README.it.md §1)
2. **GitHub secrets**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PLAYTOMIC_RELAY_TOKEN`
   (optional member-view secrets in README.it.md §2–4)
3. **Cloudflare Worker relay** (`relay/`): Playtomic's WAF blocks datacenter IPs, so GitHub
   runners must go through a small free Worker that forwards the availability endpoint and
   requires a shared `X-Relay-Token`. In local runs the monitor talks to Playtomic directly

That's it — the workflow schedules itself. Trigger it manually from the Actions tab for a
test run.

## Configuration

Everything lives in `config.json`: club, days ahead, courts, surfaces, watch windows and
notification settings. No dependencies to install: `monitor.py` runs on any stock Python ≥ 3.9.

## Notes

- The monitor reads Playtomic's **public** availability endpoint; be respectful with polling
  frequency and comply with Playtomic's terms of service
- Never commit tokens or cookies: all credentials live in GitHub Secrets (the repo is public)

## License

[MIT](LICENSE)
