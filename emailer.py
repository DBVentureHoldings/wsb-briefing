"""Send the daily briefing via Gmail SMTP using an app password."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send(subject: str, html: str, markdown: str) -> None:
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipients = [r.strip() for r in os.environ["RECIPIENTS"].split(",") if r.strip()]
    if not recipients:
        raise RuntimeError("RECIPIENTS is empty")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.set_content(markdown)  # plain-text fallback
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, password)
        s.send_message(msg)
