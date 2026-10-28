import logging
import os
import smtplib
from email.message import EmailMessage

import requests

log = logging.getLogger("monitor")


def send_email(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.getenv("SMTP_FROM") or os.environ["SMTP_USER"]
    msg["To"] = os.environ["NOTIFY_EMAIL"]
    msg.set_content(body)

    # repository secrets that aren't set reach the Actions job as empty strings,
    # so a getenv default would never kick in
    host = os.getenv("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.getenv("SMTP_PORT") or 587)
    with smtplib.SMTP(host, port, timeout=15) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)


def send_telegram(subject, body):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": f"{subject}\n\n{body}"},
        timeout=10,
    )
    r.raise_for_status()


CHANNELS = {
    "email": (send_email, ["SMTP_USER", "SMTP_PASSWORD", "NOTIFY_EMAIL"]),
    "telegram": (send_telegram, ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]),
}


def enabled():
    return [name for name, (_, keys) in CHANNELS.items() if all(os.getenv(k) for k in keys)]


def send_all(subject, body):
    channels = enabled()
    if not channels:
        print(f"\n{subject}\n{body}\n", flush=True)
        return []

    sent = []
    for name in channels:
        try:
            CHANNELS[name][0](subject, body)
            sent.append(name)
        except Exception as e:
            # the check is already saved, so a broken channel only costs this one alert
            log.error("%s alert failed: %s", name, e)
    return sent
