"""Read-only queries behind the terminal's panels."""
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import Alert, CoinTick, CollectorRun, DexTick, Item, ItemAnalysis, iso, utcnow


def item_row(item: Item, analysis: ItemAnalysis | None) -> dict:
    return {
        "id": item.id,
        "source": item.source,
        "kind": item.kind,
        "title": item.title,
        "url": item.url,
        "published_at": iso(item.published_at or item.fetched_at),
        "importance": analysis.importance if analysis else None,
        "sentiment": analysis.sentiment if analysis else None,
        "category": analysis.category if analysis else None,
        "coins": analysis.coins if analysis else [],
        "summary": analysis.summary if analysis else "",
    }


def alert_row(a: Alert) -> dict:
    return {
        "id": a.id, "kind": a.kind, "severity": a.severity, "title": a.title, "detail": a.detail,
        "url": a.url, "symbol": a.symbol, "chain": a.chain, "created_at": iso(a.created_at),
    }


def run_row(r: CollectorRun) -> dict:
    return {
        "id": r.id, "collector": r.collector, "started_at": iso(r.started_at),
        "seconds": round((r.finished_at - r.started_at).total_seconds(), 1),
        "fetched": r.fetched, "stored": r.stored, "error": r.error,
    }


def feed(session: Session, importance: str | None = None, kind: str | None = None, q: str | None = None,
         after_id: int = 0, limit: int = 100, include_noise: bool = False) -> list[dict]:
    stmt = select(Item, ItemAnalysis).outerjoin(ItemAnalysis, ItemAnalysis.item_id == Item.id).where(Item.id > after_id)
    if importance == "unrated":
        stmt = stmt.where(ItemAnalysis.id.is_(None))
    elif importance:
        stmt = stmt.where(ItemAnalysis.importance == importance)
    elif not include_noise:
        stmt = stmt.where(or_(ItemAnalysis.importance.is_(None), ItemAnalysis.importance != "noise"))
    if kind:
        stmt = stmt.where(Item.kind == kind)
    if q:
        stmt = stmt.where(Item.title.ilike(f"%{q}%"))
    stmt = stmt.order_by(Item.id.desc()).limit(limit)
    return [item_row(i, a) for i, a in session.execute(stmt)]


def alerts(session: Session, after_id: int = 0, limit: int = 100) -> list[dict]:
    rows = session.scalars(select(Alert).where(Alert.id > after_id).order_by(Alert.id.desc()).limit(limit))
    return [alert_row(a) for a in rows]


def runs(session: Session, after_id: int = 0, limit: int = 100) -> list[dict]:
    rows = session.scalars(select(CollectorRun).where(CollectorRun.id > after_id)
                           .order_by(CollectorRun.id.desc()).limit(limit))
    return [run_row(r) for r in rows]


def latest_coins(session: Session) -> list[CoinTick]:
    last = session.scalar(select(func.max(CoinTick.taken_at)))
    if last is None:
        return []
    return list(session.scalars(
        select(CoinTick).where(CoinTick.taken_at == last)
        .order_by(CoinTick.market_cap_usd.desc().nulls_last())
    ))


def coin_row(c: CoinTick) -> dict:
    return {
        "id": c.coin_id, "symbol": c.symbol, "name": c.name, "price": c.price_usd,
        "market_cap": c.market_cap_usd, "volume": c.volume_24h_usd,
        "ch1h": c.change_1h_pct, "ch24h": c.change_24h_pct, "ch7d": c.change_7d_pct,
    }


def overview(session: Session) -> dict:
    coins = latest_coins(session)
    total_cap = sum(c.market_cap_usd or 0 for c in coins)
    weighted = (
        sum((c.market_cap_usd or 0) * (c.change_24h_pct or 0) for c in coins) / total_cap if total_cap else None
    )
    with_change = [c for c in coins if c.change_24h_pct is not None]
    breadth = sum(c.change_24h_pct > 0 for c in with_change) / len(with_change) if with_change else None

    since = utcnow() - timedelta(hours=6)
    mood = dict(session.execute(
        select(ItemAnalysis.sentiment, func.count())
        .where(ItemAnalysis.created_at >= since, ItemAnalysis.importance != "noise")
        .group_by(ItemAnalysis.sentiment)
    ).all())

    liquid = [c for c in coins if (c.volume_24h_usd or 0) >= 1_000_000]

    def movers(key, reverse):
        ranked = sorted((c for c in liquid if getattr(c, key) is not None), key=lambda c: getattr(c, key), reverse=reverse)
        return [coin_row(c) for c in ranked[:8]]

    last_dex = session.scalar(select(func.max(DexTick.taken_at)))
    dex = [] if last_dex is None else session.scalars(
        select(DexTick).where(DexTick.taken_at == last_dex)
        .order_by(DexTick.volume_24h_usd.desc().nulls_last()).limit(15)
    ).all()

    return {
        "updated_at": iso(coins[0].taken_at) if coins else None,
        "market": {
            "total_cap": total_cap or None,
            "weighted_change_24h": weighted,
            "breadth_up": breadth,
            "news_bullish": mood.get("bullish", 0),
            "news_bearish": mood.get("bearish", 0),
            "news_neutral": mood.get("neutral", 0),
        },
        "majors": [coin_row(c) for c in coins[:12]],
        "gainers_1h": movers("change_1h_pct", True),
        "gainers_24h": movers("change_24h_pct", True),
        "losers_24h": movers("change_24h_pct", False),
        "dex_trending": [{
            "symbol": d.base_symbol, "chain": d.chain, "price": d.price_usd, "liquidity": d.liquidity_usd,
            "volume": d.volume_24h_usd, "ch24h": d.change_24h_pct, "url": d.url,
        } for d in dex],
    }
