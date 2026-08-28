"""SMTP 이메일 알림."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from ..config import EmailConfig
from .base import Channel, Message


class EmailChannel(Channel):
    name = "email"

    def __init__(self, cfg: EmailConfig, timeout: float = 20.0) -> None:
        self.cfg = cfg
        self.timeout = timeout

    @property
    def usable(self) -> bool:
        return self.cfg.usable

    def build(self, message: Message) -> EmailMessage:
        mail = EmailMessage()
        mail["Subject"] = message.title
        mail["From"] = self.cfg.sender or self.cfg.username
        mail["To"] = ", ".join(self.cfg.recipients)
        mail.set_content(message.as_text())
        if message.screenshot and message.screenshot.exists():
            mail.add_attachment(
                message.screenshot.read_bytes(),
                maintype="image",
                subtype="png",
                filename=message.screenshot.name,
            )
        return mail

    def _send(self, message: Message) -> None:
        mail = self.build(message)
        with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=self.timeout) as smtp:
            if self.cfg.use_tls:
                smtp.starttls()
            if self.cfg.username:
                smtp.login(self.cfg.username, self.cfg.password)
            smtp.send_message(mail)
