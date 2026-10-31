"""Pushes alerts to your phone through ntfy and to a Telegram channel."""
import logging
from datetime import timedelta
from html import escape

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


def telegram_headline(alert: Alert) -> str:
    if alert.kind == "rug_risk":
        # A liquidity pull is the rug itself; a contract flagged by RugCheck/GoPlus is only a warning sign.
        if alert.dedupe_key.startswith("liqpull:"):
            return "🚨 RUG PULL"
        return "⚠️ POSSIBLE RUG PULL"
    if alert.kind == "hack":
        return "🚨 HACK" if alert.severity == "critical" else "⚠️ HACK"
    if alert.kind == "picks":
        return "🏃 DAILY RUNNERS"
    return f"{ICON.get(alert.severity, '🔔')} {alert.kind.replace('_', ' ').upper()}"


def telegram_text(alert: Alert) -> str:
    parts = [f"<b>{escape(telegram_headline(alert))}</b>", escape(alert.title)]
    if alert.detail:
        parts.append(escape(alert.detail))
    if alert.url and alert.url.startswith("http"):
        parts.append(f'<a href="{escape(alert.url)}">Open</a>')
    return "\n".join(parts)


class Notifier(Job):
    name = "notifier"
    interval_seconds = 30

    def __init__(self, ntfy_topic: str = settings.ntfy_topic, ntfy_server: str = settings.ntfy_server,
                 telegram_token: str = settings.telegram_bot_token, telegram_chat: str = settings.telegram_chat_id,
                 min_severity: str = settings.notify_min_severity, kinds: set[str] | None = None,
                 telegram_kinds: set[str] | None = None):
        self.ntfy_url = f"{ntfy_server.rstrip('/')}/{ntfy_topic}" if ntfy_topic else ""
        self.telegram_token = telegram_token
        self.telegram_chat = telegram_chat
        self.min_severity = min_severity
        self.kinds = kinds if kinds is not None else settings.notify_kinds
        self.telegram_kinds = telegram_kinds if telegram_kinds is not None else settings.telegram_kinds

    @property
    def configured(self) -> bool:
        return bool(self.ntfy_url or (self.telegram_token and self.telegram_chat))

    def channels(self, alert: Alert) -> list[str]:
        """Where this alert goes: ntfy follows the severity filter, Telegram its own list of kinds."""
        out = []
        if self.ntfy_url and should_send(alert, self.min_severity, self.kinds):
            out.append("ntfy")
        if self.telegram_token and self.telegram_chat and alert.kind in self.telegram_kinds:
            out.append("telegram")
        return out

    async def send_ntfy(self, client: httpx.AsyncClient, alert: Alert) -> None:
        headers = {
            "Title": alert.kind.replace("_", " "),
            "Priority": {"critical": "5", "warn": "4"}.get(alert.severity, "3"),
            "Tags": f"{NTFY_TAG.get(alert.severity, 'bell')},{alert.kind}",
        }
        if alert.url and alert.url.isascii():
            headers["Click"] = alert.url
        body = alert.title + (f"\n{alert.detail}" if alert.detail else "")
        (await client.post(self.ntfy_url, content=body.encode(), headers=headers)).raise_for_status()

    async def send_telegram(self, client: httpx.AsyncClient, alert: Alert) -> None:
        resp = await client.post(f"https://api.telegram.org/bot{self.telegram_token}/sendMessage", json={
            "chat_id": self.telegram_chat, "text": telegram_text(alert),
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })
        resp.raise_for_status()

    async def send(self, client: httpx.AsyncClient, alert: Alert, channels: list[str]) -> bool:
        """Returns False only if every channel failed, so the alert is retried next round."""
        delivered = False
        for channel in channels:
            try:
                await (self.send_ntfy if channel == "ntfy" else self.send_telegram)(client, alert)
                delivered = True
            except httpx.HTTPError as exc:
                # Don't log exc itself for Telegram: its URL contains the bot token.
                reason = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
                log.warning("%s notify failed for alert %s: %s", channel, alert.id, reason)
        return delivered

    async def run(self, client: httpx.AsyncClient, sessions: sessionmaker[Session]) -> tuple[int, int]:
        sent = 0
        with sessions() as session:
            pending = session.scalars(select(Alert).where(Alert.notified_at.is_(None)).order_by(Alert.id)).all()
            # Don't flood your phone with a backlog after downtime or on first start.
            stale = utcnow() - timedelta(minutes=30)
            for alert in pending:
                created = alert.created_at if alert.created_at.tzinfo else alert.created_at.replace(tzinfo=stale.tzinfo)
                channels = self.channels(alert) if created >= stale else []
                if channels:
                    if not await self.send(client, alert, channels):
                        break  # try again next round, in order
                    sent += 1
                alert.notified_at = utcnow()
                session.commit()
        return len(pending), sent
