#!/usr/bin/env python3
"""Playtomic slot monitor.

Polls the public availability API of a Playtomic club and sends an email +
WhatsApp (CallMeBot) notification when a slot matching the watch windows in
config.json becomes available.

Usage:
    python3 monitor.py             # check and notify
    python3 monitor.py --dry-run   # check, print what would be sent, no state write
    python3 monitor.py --selftest  # run the window-matching self-check
"""
import html
import json
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "state.json")
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABELS_IT = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
# Playtomic's CloudFront WAF 403s datacenter IPs (GitHub runners) and even plain
# non-browser clients; PLAYTOMIC_BASE routes requests through a Cloudflare Worker
# relay (its egress clears the WAF) when set. Booking links stay on playtomic.com
# (opened from the user's own browser).
_DEFAULT_BASE = "https://playtomic.com"
PLAYTOMIC_BASE = os.environ.get("PLAYTOMIC_BASE", _DEFAULT_BASE).rstrip("/")
_RELAY_TOKEN = os.environ.get("PLAYTOMIC_RELAY_TOKEN")
RELAY_HEADERS = {"X-Relay-Token": _RELAY_TOKEN} if _RELAY_TOKEN else {}
# Auth + member availability live on api.playtomic.io. When the relay is
# configured it proxies that host too (same path prefixes), so route through
# PLAYTOMIC_BASE; otherwise (local dev) hit api.playtomic.io directly.
API_BASE = PLAYTOMIC_BASE if PLAYTOMIC_BASE != _DEFAULT_BASE else "https://api.playtomic.io"


def http_get(url, extra_headers=None):
    headers = dict(HEADERS)
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def playtomic_login():
    """Return a bearer token for the member view, or None for anonymous mode."""
    email = os.environ.get("PLAYTOMIC_EMAIL")
    password = os.environ.get("PLAYTOMIC_PASSWORD")
    if not (email and password):
        print("info: PLAYTOMIC_EMAIL/PLAYTOMIC_PASSWORD not set, anonymous mode", file=sys.stderr)
        return None
    try:
        req = urllib.request.Request(
            f"{API_BASE}/v3/auth/login",
            data=json.dumps({"email": email, "password": password}).encode(),
            headers={**HEADERS, **RELAY_HEADERS, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)["access_token"]
    except Exception as e:  # ponytail: on any login trouble, degrade to anonymous
        print(f"warn: playtomic login failed, falling back to anonymous: {e}", file=sys.stderr)
        return None


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


def fetch_day(cfg, day, token=None):
    """Return [(resource_id, local_start_datetime, duration_min)] for one day.

    With a token, uses the authenticated API (user_id=me): members see
    preemption days the public API does not expose yet.
    """
    if token:
        qs = urllib.parse.urlencode({
            "tenant_id": cfg["tenant_id"],
            "sport_id": cfg["sport_id"],
            "user_id": "me",
            "local_start_min": f"{day.isoformat()}T00:00:00",
            "local_start_max": f"{day.isoformat()}T23:59:59",
        })
        data = json.loads(http_get(f"{API_BASE}/v1/availability?{qs}",
                                   {**RELAY_HEADERS, "Authorization": f"Bearer {token}"}))
    else:
        qs = urllib.parse.urlencode({
            "tenant_id": cfg["tenant_id"],
            "date": day.isoformat(),
            "sport_id": cfg["sport_id"],
        })
        data = json.loads(http_get(f"{PLAYTOMIC_BASE}/api/clubs/availability?{qs}", RELAY_HEADERS))
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
    token = playtomic_login()
    slots = {}
    today = date.today()
    for offset in range(cfg["days_ahead"] + 1):
        day = today + timedelta(days=offset)
        try:
            day_slots = fetch_day(cfg, day, token)
        except Exception as e:
            print(f"warn: fetch failed for {day}: {e}", file=sys.stderr)
            if not token:
                continue
            try:  # authenticated endpoint hiccup: retry the day anonymously
                day_slots = fetch_day(cfg, day)
            except Exception:
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


def format_lines(slots):
    ordered = sorted(slots.values(), key=lambda s: (s[1], s[0]))
    return [
        f"{name} — {DAY_LABELS_IT[dt.weekday()]} {dt.strftime('%d/%m %H:%M')} ({duration} min)"
        for name, dt, duration in ordered
    ]


def send_email(cfg, lines, first_date):
    user = os.environ.get("GMAIL_USER")
    pwd = os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and pwd):
        print("warn: GMAIL_USER/GMAIL_APP_PASSWORD not set, skipping email", file=sys.stderr)
        return
    link = f"https://playtomic.com/clubs/{cfg['club_slug']}?date={first_date}"
    msg = EmailMessage()
    msg["Subject"] = f"Slot liberi: {cfg['club_name']} ({len(lines)})"
    msg["From"] = user
    msg["To"] = os.environ.get("MAIL_TO", user)
    msg.set_content("Nuovi slot disponibili:\n\n" + "\n".join(lines) + f"\n\nPrenota: {link}")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, pwd)
        smtp.send_message(msg)
    print(f"email sent to {msg['To']}")


def format_telegram(cfg, slots, link, max_lines=30):
    """HTML message grouped by day: bold hour, court name with surface info."""
    ordered = sorted(slots.values(), key=lambda s: (s[1], s[0]))
    dropped = max(0, len(ordered) - max_lines)
    parts = [f"🎾 <b>{html.escape(cfg['club_name'])}</b> — nuovi slot liberi"]
    last_day = None
    for name, dt, duration in ordered[:max_lines]:
        if dt.date() != last_day:
            last_day = dt.date()
            parts.append(f"\n📅 <b>{DAY_LABELS_IT[dt.weekday()]} {dt.strftime('%d/%m')}</b>")
        parts.append(f"    <b>{dt.strftime('%H:%M')}</b> · {html.escape(name)} — {duration} min")
    if dropped:
        parts.append(f"\n… e altri {dropped} slot")
    parts.append(f'\n<a href="{link}">👉 Prenota su Playtomic</a>')
    return "\n".join(parts)


def send_telegram(cfg, slots, first_date):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print("warn: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set, skipping Telegram", file=sys.stderr)
        return
    link = f"https://playtomic.com/clubs/{cfg['club_slug']}?date={first_date}"
    text = format_telegram(cfg, slots, link)
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                   "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30):
        pass
    print("telegram sent")


def send_whatsapp(cfg, lines, first_date):
    phone = os.environ.get("CALLMEBOT_PHONE")
    apikey = os.environ.get("CALLMEBOT_APIKEY")
    if not (phone and apikey):
        print("warn: CALLMEBOT_PHONE/CALLMEBOT_APIKEY not set, skipping WhatsApp", file=sys.stderr)
        return
    link = f"https://playtomic.com/clubs/{cfg['club_slug']}?date={first_date}"
    text = f"🎾 {cfg['club_name']} — nuovi slot:\n" + "\n".join(lines[:15])
    if len(lines) > 15:
        text += f"\n… e altri {len(lines) - 15}"
    text += f"\n{link}"
    url = ("https://api.callmebot.com/whatsapp.php?"
           + urllib.parse.urlencode({"phone": phone, "text": text, "apikey": apikey}))
    http_get(url)
    print(f"whatsapp sent to {phone}")


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
        new_slots = {k: current[k] for k in new_keys}
        lines = format_lines(new_slots)
        first_date = min(s[1] for s in new_slots.values()).date().isoformat()
        if dry_run:
            print("dry-run, would notify:")
            print("\n".join(lines))
        else:
            send_email(cfg, lines, first_date)
            send_telegram(cfg, new_slots, first_date)
            send_whatsapp(cfg, lines, first_date)

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
    # Telegram formatting: slots on two days -> two day headers, court info kept
    cfg = {"club_name": "Club Test", "club_slug": "club-test"}
    slots = {
        "a": ("Campo 1 (terra)", datetime(2026, 7, 6, 18, 30, tzinfo=tz), 60),
        "b": ("Campo 2E (quick, singolo)", datetime(2026, 7, 7, 7, 0, tzinfo=tz), 60),
    }
    msg = format_telegram(cfg, slots, "https://example.com")
    assert msg.count("📅") == 2
    assert "Campo 2E (quick, singolo)" in msg and "<b>07:00</b>" in msg
    assert "EUR" not in msg and 'href="https://example.com"' in msg
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
