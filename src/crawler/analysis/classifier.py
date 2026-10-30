import json
import logging
from typing import Literal

import anthropic
import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings
from ..db import Item, ItemAnalysis, add_alert, utcnow
from ..jobs import Job

log = logging.getLogger(__name__)

Category = Literal[
    "regulation", "hack", "scam", "macro", "exchange", "listing", "etf", "partnership",
    "tech", "whale", "market", "legal", "other",
]

SYSTEM = """You are the analyst for a crypto trader's news bot. For each item you get, judge how much it could move crypto prices.

importance:
- major: likely to move the whole market or a top-50 coin within days. Examples: ETF decisions, big exchange hacks or insolvencies, major regulation or enforcement, Fed/macro surprises, large unlocks, top-coin network outages.
- minor: relevant to specific coins or traders but unlikely to move the market. Examples: mid-cap partnerships, small exploits, listings, upgrades.
- noise: opinion pieces, price recaps, promotions, sponsored content, memes, questions, anything with no new information.

sentiment: bullish, bearish or neutral for the coins it affects (or the market, if no coin is named).

coins: ticker symbols in upper case (BTC, ETH, SOL, ...). Use an empty list if it's about the market as a whole.

summary: one plain sentence a trader can read in two seconds. Say what happened, not what to do.

confidence: 0 to 1, how sure you are about importance and sentiment together.

Return exactly one verdict per item, using the item's id. Judge the content, not the headline's tone; price recaps describing a move that already happened are noise."""


class Verdict(BaseModel):
    id: int
    importance: Literal["major", "minor", "noise"]
    sentiment: Literal["bullish", "bearish", "neutral"]
    category: Category
    coins: list[str]
    summary: str
    confidence: float


class Verdicts(BaseModel):
    verdicts: list[Verdict]


def format_items(items: list[Item]) -> str:
    return json.dumps([
        {
            "id": i.id,
            "source": i.source,
            "kind": i.kind,
            "title": i.title,
            "published": i.published_at.isoformat() if i.published_at else None,
            "text": (i.summary or "")[:1200],
        }
        for i in items
    ], ensure_ascii=False)


def save_verdicts(session: Session, items: list[Item], verdicts: list[Verdict], model: str) -> int:
    by_id = {i.id: i for i in items}
    stored = 0
    for v in verdicts:
        item = by_id.get(v.id)
        if item is None or item.analysed_at:
            continue
        coins = sorted({c.strip().upper().lstrip("$") for c in v.coins if c.strip()})
        session.add(ItemAnalysis(
            item_id=item.id, importance=v.importance, sentiment=v.sentiment, category=v.category,
            coins=coins, summary=v.summary, confidence=max(0.0, min(1.0, v.confidence)), model=model,
        ))
        item.analysed_at = utcnow()
        stored += 1
        if v.importance == "major" or v.category in ("hack", "scam"):
            add_alert(
                session, f"item:{item.id}",
                kind="hack" if v.category in ("hack", "scam") else "major_news",
                severity="critical" if v.importance == "major" else "warn",
                title=v.summary or item.title,
                detail=f"{v.sentiment} · {', '.join(coins) or 'market'} · {item.source}",
                url=item.url,
                symbol=",".join(coins),
            )
    # Anything Claude skipped or refused gets marked so it isn't retried forever.
    for item in items:
        if not item.analysed_at:
            item.analysed_at = utcnow()
    session.commit()
    return stored


class Classifier(Job):
    """Tags each new item as major/minor/noise and bullish/bearish, and names the coins it affects."""

    name = "classifier"
    interval_seconds = 60

    def __init__(self, client: anthropic.AsyncAnthropic | None = None, model: str = settings.anthropic_model,
                 batch_size: int = 25, max_batches: int = 8):
        self.client = client or anthropic.AsyncAnthropic()
        self.model = model
        self.batch_size = batch_size
        self.max_batches = max_batches

    async def classify(self, items: list[Item]) -> list[Verdict]:
        response = await self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": format_items(items)}],
            output_format=Verdicts,
            output_config={"effort": settings.anthropic_effort},
            # If a request is declined, the API retries it on a fallback model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            log.warning("classifier got no verdicts (stop_reason=%s)", response.stop_reason)
            return []
        return response.parsed_output.verdicts

    async def run(self, client: httpx.AsyncClient, sessions: sessionmaker[Session]) -> tuple[int, int]:
        seen = stored = 0
        for _ in range(self.max_batches):
            with sessions() as session:
                items = list(session.scalars(
                    select(Item).where(Item.analysed_at.is_(None))
                    .order_by(Item.published_at.desc().nulls_last(), Item.id.desc())
                    .limit(self.batch_size)
                ))
                if not items:
                    break
                verdicts = await self.classify(items)
                seen += len(items)
                stored += save_verdicts(session, items, verdicts, self.model)
        return seen, stored
