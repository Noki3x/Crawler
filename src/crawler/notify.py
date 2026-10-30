"""Pushes alerts to your phone through ntfy and/or Telegram."""
import logging
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .db import Alert, utcnow
from .jobs import Job

log = logging.getLogger(__name__)

SEVERITY = {"info": 0, "warn": 1, "critical": 2}
# Sent whatever their severity: the daily picks and market call flips.
ALWAYS_SEND = {"picks", "market_call"}
ICON = {"critical": "🚨", "warn": "⚠️", "info": "📈"}
# ntfy headers must be ASCII, so icons go in as emoji short codes.
NTFY_TAG = {"critical": "rotating_light", "warn": "warning", "info": "chart_with_upwards_trend"}


def should_send(alert: Alert, min_severity: str, kinds: set[str]) -> bool:
    if alert.kind in ALWAYS_SEND or alert.kind in kinds:
        return True
    return SEVERITY.get(alert.severity, 0) >= SEVERITY.get(min_severity, 2)


class Notifier(Job):
    name = "notifier"
    interval_seconds = 30

    def __init__(self, ntfy_topic: str = settings.ntfy_topic, ntfy_server: str = settings.ntfy_server,
                 telegram_token: str = settings.telegram_bot_token, telegram_chat: str = settings.telegram_chat_id,
                 min_severity: str = settings.notify_min_severity, kinds: set[str] | None = None):
        self.ntfy_url = f"{ntfy_server.rstrip('/')}/{ntfy_topic}" if ntfy_topic else ""
        self.telegram_token = telegram_token
        self.telegram_chat = telegram_chat
        self.min_severity = min_severity
        self.kinds = kinds if kinds is not None else settings.notify_kinds

    @property
    def configured(self) -> bool:
        return bool(self.ntfy_url or (self.telegram_token and self.telegram_chat))

    async def send(self, client: httpx.AsyncClient, alert: Alert) -> None:
        if self.ntfy_url:
            headers = {
                "Title": alert.kind.replace("_", " "),
                "Priority": {"critical": "5", "warn": "4"}.get(alert.severity, "3"),
                "Tags": f"{NTFY_TAG.get(alert.severity, 'bell')},{alert.kind}",
            }
            if alert.url and alert.url.isascii():
                headers["Click"] = alert.url
            body = alert.title + (f"\n{alert.detail}" if alert.detail else "")
            (await client.post(self.ntfy_url, content=body.encode(), headers=headers)).raise_for_status()
        if self.telegram_token and self.telegram_chat:
            text = f"{ICON.get(alert.severity, '')} {alert.title}"
            if alert.detail:
                text += f"\n{alert.detail}"
            if alert.url:
                text += f"\n{alert.url}"
            resp = await client.post(f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                                     json={"chat_id": self.telegram_chat, "text": text, "disable_web_page_preview": True})
            resp.raise_for_status()

    async def run(self, client: httpx.AsyncClient, sessions: sessionmaker[Session]) -> tuple[int, int]:
        sent = 0
        with sessions() as session:
            pending = session.scalars(select(Alert).where(Alert.notified_at.is_(None)).order_by(Alert.id)).all()
            # Don't flood your phone with a backlog after downtime or on first start.
            stale = utcnow() - timedelta(minutes=30)
            for alert in pending:
                created = alert.created_at if alert.created_at.tzinfo else alert.created_at.replace(tzinfo=stale.tzinfo)
                if created >= stale and should_send(alert, self.min_severity, self.kinds):
                    try:
                        await self.send(client, alert)
                    except httpx.HTTPError as exc:
                        log.warning("notify failed for alert %s: %s", alert.id, exc)
                        break  # try again next round, in order
                    sent += 1
                alert.notified_at = utcnow()
                session.commit()
        return len(pending), sent
