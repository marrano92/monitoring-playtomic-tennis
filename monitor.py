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
import json
import os
import re
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


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def load_config():
    with open(os.path.join(BASE, "config.json")) as f:
        return json.load(f)


def court_names(cfg):
    """Map resource_id -> court name, scraped once from the club page."""
    try:
        html = http_get(f"https://playtomic.com/clubs/{cfg['club_slug']}")
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html, re.S)
        resources = json.loads(m.group(1))["props"]["pageProps"]["tenant"]["resources"]
        return {r["resourceId"]: r["name"].strip() for r in resources}
    except Exception as e:  # ponytail: names are cosmetic, fall back to raw ids
        print(f"warn: could not fetch court names: {e}", file=sys.stderr)
        return {}


def fetch_day(cfg, day):
    """Return [(resource_id, local_start_datetime, duration_min, price)] for one day."""
    qs = urllib.parse.urlencode({
        "tenant_id": cfg["tenant_id"],
        "date": day.isoformat(),
        "sport_id": cfg["sport_id"],
    })
    data = json.loads(http_get(f"https://playtomic.com/api/clubs/availability?{qs}"))
    tz = ZoneInfo(cfg["timezone"])
    out = []
    for res in data:
        for slot in res["slots"]:
            # API times are UTC; combine date+time and convert to club timezone.
            utc = datetime.fromisoformat(f"{res['start_date']}T{slot['start_time']}+00:00")
            out.append((res["resource_id"], utc.astimezone(tz),
                        slot["duration"], slot.get("price", "")))
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
        for rid, local_dt, duration, price in day_slots:
            name = names.get(rid, rid[:8])
            if wanted_courts and name not in wanted_courts:
                continue
            if not in_window(local_dt, cfg["watch_windows"]):
                continue
            key = f"{rid}|{local_dt.isoformat()}|{duration}"
            slots[key] = (name, local_dt, duration, price)
    return slots


def format_lines(slots):
    ordered = sorted(slots.values(), key=lambda s: (s[1], s[0]))
    return [
        f"{name} — {DAY_LABELS_IT[dt.weekday()]} {dt.strftime('%d/%m %H:%M')}"
        f" ({duration} min, {price})"
        for name, dt, duration, price in ordered
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


def send_telegram(cfg, lines, first_date):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print("warn: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set, skipping Telegram", file=sys.stderr)
        return
    link = f"https://playtomic.com/clubs/{cfg['club_slug']}?date={first_date}"
    text = f"🎾 {cfg['club_name']} — nuovi slot:\n" + "\n".join(lines[:30])
    if len(lines) > 30:
        text += f"\n… e altri {len(lines) - 30}"
    text += f"\n{link}"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
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
            send_telegram(cfg, lines, first_date)
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
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
