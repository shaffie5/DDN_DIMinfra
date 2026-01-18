from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Iterable


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def email_enabled() -> bool:
    return bool(os.getenv("DDN_SMTP_HOST") and os.getenv("DDN_FROM_EMAIL"))


def send_email(
    to_addrs: Iterable[str],
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    host = os.getenv("DDN_SMTP_HOST")
    port = int(os.getenv("DDN_SMTP_PORT", "587"))
    user = os.getenv("DDN_SMTP_USER")
    password = os.getenv("DDN_SMTP_PASS")
    use_tls = _env_bool("DDN_SMTP_TLS", True)
    from_email = os.getenv("DDN_FROM_EMAIL")

    if not host or not from_email:
        raise RuntimeError("Email not configured: set DDN_SMTP_HOST and DDN_FROM_EMAIL")

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = ", ".join([a for a in to_addrs if a])
    msg["Subject"] = subject
    msg.set_content(body)

    if attachments:
        for filename, content, mime in attachments:
            maintype, subtype = mime.split("/", 1)
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    with smtplib.SMTP(host, port) as smtp:
        if use_tls:
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)
