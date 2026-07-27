#!/usr/bin/env python3
"""Playtomic slot monitor.

Polls the public availability API of a Playtomic club and sends a Telegram
notification when a slot matching the watch windows in config.json becomes
available.

Usage:
    python3 monitor.py             # check and notify
    python3 monitor.py --dry-run   # check, print what would be sent, no state write
    python3 monitor.py --selftest  # run the window-matching self-check
"""
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "state.json")
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
# playtomic.com's CloudFront WAF 403s datacenter IPs (GitHub runners) and even
# plain non-browser clients; PLAYTOMIC_BASE routes the availability GET through a
# Cloudflare Worker relay (its egress clears that WAF) when set. Booking links
# stay on playtomic.com (opened from the user's own browser).
PLAYTOMIC_BASE = os.environ.get("PLAYTOMIC_BASE", "https://playtomic.com").rstrip("/")
_RELAY_TOKEN = os.environ.get("PLAYTOMIC_RELAY_TOKEN")
RELAY_HEADERS = {"X-Relay-Token": _RELAY_TOKEN} if _RELAY_TOKEN else {}
# Optional logged-in cookie ("pt_auth_access_token=..."). The same public
# availability endpoint returns the member view — free slots ~10 days out vs ~3
# anonymous — when this cookie rides along. Playtomic access tokens live ~1h and
# can only be refreshed via api.playtomic.io (which WAF-blocks the relay too), so
# this is a manual top-up: paste a fresh cookie when actively hunting. An expired
# or absent cookie just yields the anonymous view (HTTP 200, no error), so it
# degrades safely on its own.
PLAYTOMIC_COOKIE = os.environ.get("PLAYTOMIC_COOKIE")


def http_get(url, extra_headers=None):
    headers = dict(HEADERS)
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def load_config():
    with open(os.path.join(BASE, "config.json")) as f:
        return json.load(f)


def court_names(cfg):
    """resource_id -> court name, from the static map in config.json.

    A live refresh from the club HTML page used to run here, but playtomic.com's
    CloudFront WAF 403s that page (even through the relay), so it only produced
    noise. The static map is complete; unknown ids degrade to rid[:8] in
    collect_matching. Add a new court to config.json when the club adds one.
    """
    return dict(cfg.get("court_names", {}))


def fetch_day(cfg, day):
    """Return [(resource_id, local_start_datetime, duration_min)] for one day.

    Hits the public availability endpoint; PLAYTOMIC_COOKIE, when set, unlocks the
    member view (further-out days) on the very same endpoint.
    """
    qs = urllib.parse.urlencode({
        "tenant_id": cfg["tenant_id"],
        "date": day.isoformat(),
        "sport_id": cfg["sport_id"],
    })
    headers = dict(RELAY_HEADERS)
    if PLAYTOMIC_COOKIE:
        headers["Cookie"] = PLAYTOMIC_COOKIE
    data = json.loads(http_get(f"{PLAYTOMIC_BASE}/api/clubs/availability?{qs}", headers))
    tz = ZoneInfo(cfg["timezone"])
    out = []
    for res in data:
        for slot in res["slots"]:
            # API times are UTC; combine date+time and convert to club timezone.
            utc = datetime.fromisoformat(f"{res['start_date']}T{slot['start_time']}+00:00")
            out.append((res["resource_id"], utc.astimezone(tz), slot["duration"]))
    return out


def in_window(local_dt, windows):
    day = WEEKDAYS[local_dt.weekday()]
    hm = local_dt.strftime("%H:%M")
    return any(day in w["days"] and w["from"] <= hm < w["to"] for w in windows)


def collect_matching(cfg):
    """All currently free slots matching windows/courts, keyed for dedup."""
    names = court_names(cfg)
    wanted_courts = set(cfg.get("courts") or [])
    slots = {}
    today = date.today()
    for offset in range(cfg["days_ahead"] + 1):
        day = today + timedelta(days=offset)
        try:
            day_slots = fetch_day(cfg, day)
        except Exception as e:
            print(f"warn: fetch failed for {day}: {e}", file=sys.stderr)
            continue
        for rid, local_dt, duration in day_slots:
            name = names.get(rid, rid[:8])
            if wanted_courts and name not in wanted_courts:
                continue
            if not in_window(local_dt, cfg["watch_windows"]):
                continue
            key = f"{rid}|{local_dt.isoformat()}|{duration}"
            slots[key] = (name, local_dt, duration)
    return slots


def court_parts(name):
    """'Campo 5 (terra)' -> ('Campo 5', 'terra'); no parenthesis -> (name, '')."""
    base, _, surface = name.partition(" (")
    return base.strip(), surface.rstrip(")").strip()


def court_sort_key(name):
    """Natural-ish order: Campo 1, Campo 2, Campo 10 — not 1, 10, 2."""
    m = re.search(r"\d+", name)
    return (int(m.group()) if m else 999, name)


def courts_label(names):
    """Courts of one time slot on a single line, shared surface factored out."""
    ordered = sorted(names, key=court_sort_key)
    parts = [court_parts(n) for n in ordered]
    surfaces = {s for _, s in parts}
    if len(surfaces) == 1 and "" not in surfaces:
        joined = ", ".join(html.escape(b) for b, _ in parts)
        return f"{joined} <i>({html.escape(surfaces.pop())})</i>"
    return ", ".join(html.escape(n) for n in ordered)


def group_by_day(slots):
    """[(date, [((hh:mm, duration), [court, ...]), ...]), ...], chronological."""
    days = {}
    for name, dt, duration in slots.values():
        times = days.setdefault(dt.date(), {})
        times.setdefault((dt.strftime("%H:%M"), duration), []).append(name)
    return [(day, sorted(times.items())) for day, times in sorted(days.items())]


def format_telegram(cfg, slots, max_chars=3800):
    """HTML message grouped by day and start time.

    One line per start time (courts collapsed onto it) instead of one per slot:
    a batch of 40 free slots reads as a handful of lines. Each day header links
    to that day on Playtomic. The tail is trimmed to stay under Telegram's
    4096-char cap.
    """
    days = group_by_day(slots)
    head = (f"🎾 <b>{html.escape(cfg['club_name'])}</b>\n"
            f"<i>{len(slots)} slot liberi · {len(days)} giorn{'o' if len(days) == 1 else 'i'}</i>")
    entries = []  # (line, slots on that line); day headers count 0
    for day, times in days:
        link = f"https://playtomic.com/clubs/{cfg['club_slug']}?date={day.isoformat()}"
        label = f"{DAY_NAMES_IT[day.weekday()]} {day.strftime('%d/%m')}"
        entries.append((f"\n📅 <b>{label}</b> · <a href=\"{link}\">prenota</a>", 0))
        for (hhmm, duration), courts in times:
            entries.append((f"    <b>{hhmm}</b> · {duration} min · {courts_label(courts)}",
                            len(courts)))

    def render(n):
        text = "\n".join([head] + [line for line, _ in entries[:n]])
        dropped = sum(c for _, c in entries[n:])
        return (text + f"\n\n… e altri {dropped} slot") if dropped else text

    # ponytail: re-render per dropped line, O(n²) on a list of at most a few
    # hundred entries. Bisect if a club ever frees thousands of slots at once.
    n = len(entries)
    while n > 1 and len(render(n)) > max_chars:
        n -= 1
    while n > 1 and entries[n - 1][1] == 0:  # never end on a dangling day header
        n -= 1
    return render(n)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        # Sole notification channel: failing loudly keeps state.json unwritten, so
        # the slots are re-notified next run instead of being silently swallowed.
        sys.exit("error: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                   "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30):
        pass
    print("telegram sent")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = load_config()
    current = collect_matching(cfg)
    previous = load_state()
    new_keys = set(current) - previous
    print(f"{len(current)} matching slot(s) free, {len(new_keys)} new")

    if new_keys:
        message = format_telegram(cfg, {k: current[k] for k in new_keys})
        if dry_run:
            print("dry-run, would send:\n" + message)
        else:
            send_telegram(message)

    if not dry_run:
        # State = currently free matching slots: a slot that gets booked and
        # frees up again will trigger a fresh notification.
        with open(STATE_FILE, "w") as f:
            json.dump(sorted(current), f, indent=1)


def selftest():
    windows = [
        {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "18:00", "to": "21:00"},
        {"days": ["sat", "sun"], "from": "09:00", "to": "12:00"},
    ]
    tz = ZoneInfo("Europe/Rome")
    mon_1830 = datetime(2026, 7, 6, 18, 30, tzinfo=tz)   # Monday
    mon_2100 = datetime(2026, 7, 6, 21, 0, tzinfo=tz)    # boundary: excluded
    sat_0900 = datetime(2026, 7, 11, 9, 0, tzinfo=tz)    # Saturday, boundary: included
    sat_1830 = datetime(2026, 7, 11, 18, 30, tzinfo=tz)  # Saturday evening: excluded
    assert in_window(mon_1830, windows)
    assert not in_window(mon_2100, windows)
    assert in_window(sat_0900, windows)
    assert not in_window(sat_1830, windows)
    # UTC -> Rome conversion: 16:30 UTC in July = 18:30 local (DST)
    utc = datetime.fromisoformat("2026-07-06T16:30:00+00:00")
    assert in_window(utc.astimezone(tz), windows)
    # Telegram formatting: two days -> two headers; courts sharing a start time
    # and a surface collapse onto one line, in natural court order.
    cfg = {"club_name": "Club Test", "club_slug": "club-test"}
    slots = {
        "a": ("Campo 10 (terra)", datetime(2026, 7, 6, 18, 30, tzinfo=tz), 60),
        "b": ("Campo 2 (terra)", datetime(2026, 7, 6, 18, 30, tzinfo=tz), 60),
        "c": ("Campo 2E (quick, singolo)", datetime(2026, 7, 6, 18, 30, tzinfo=tz), 90),
        "d": ("Campo 3 (terra)", datetime(2026, 7, 7, 7, 0, tzinfo=tz), 60),
    }
    msg = format_telegram(cfg, slots)
    assert msg.count("📅") == 2, msg
    assert "4 slot liberi · 2 giorni" in msg
    assert "Campo 2, Campo 10 <i>(terra)</i>" in msg          # collapsed + natural order
    assert "<b>18:30</b> · 90 min · Campo 2E <i>(quick, singolo)</i>" in msg
    assert 'href="https://playtomic.com/clubs/club-test?date=2026-07-07"' in msg
    assert msg.count("18:30") == 2 and "… e altri" not in msg  # two durations, no trim
    # Mixed surfaces on the same line keep each court's own label
    mixed = format_telegram(cfg, {
        "a": ("Campo 1 (terra)", datetime(2026, 7, 6, 18, 30, tzinfo=tz), 60),
        "b": ("Campo 1E (quick)", datetime(2026, 7, 6, 18, 30, tzinfo=tz), 60),
    })
    assert "Campo 1 (terra), Campo 1E (quick)" in mixed
    # Oversized batch: trimmed under the cap, remainder counted, no dangling header
    big = {f"k{i}": (f"Campo {i % 12 + 1} (terra)",
                     datetime(2026, 7, 6 + i % 5, 7 + i % 12, 0, tzinfo=tz), 60)
           for i in range(400)}
    trimmed = format_telegram(cfg, big, max_chars=600)
    assert len(trimmed) <= 700 and "… e altri" in trimmed, len(trimmed)
    assert not trimmed.split("\n… e altri")[0].rstrip().endswith("prenota</a>")
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
