"""Step 4: market direction, top runners, and an honest track record of both.

These are signals built from momentum, volume and news, not advice. Every call is stored and
scored after 24 hours so the terminal can show how often the bot is actually right.
"""
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .db import Alert, CoinTick, DexTick, ItemAnalysis, Prediction, TokenSafety, add_alert, iso, utcnow
from .jobs import Job

log = logging.getLogger(__name__)

# Stablecoins and wrapped/staked copies move with something else, so they're never "runners".
EXCLUDED = {
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "PYUSD", "USDD", "FRAX", "BUSD", "USD1", "RLUSD",
    "USDX", "GHO", "USDY", "USDL", "EURC", "XAUT", "PAXG", "BSC-USD", "SUSDE", "SUSDS",
    "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "RETH", "CBBTC", "CBETH", "JITOSOL", "MSOL", "BNSOL",
    "LBTC", "SOLVBTC", "WBETH", "EZETH", "RSETH", "METH", "OETH", "TBTC", "BTCB", "WBNB", "WSOL",
}
IMPORTANCE_WEIGHT = {"major": 1.0, "minor": 0.35}
SENTIMENT_SIGN = {"bullish": 1, "bearish": -1, "neutral": 0}


def zscores(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [0.0] * len(values)
    mean, sd = statistics.fmean(values), statistics.pstdev(values)
    return [0.0 if sd == 0 else max(-3.0, min(3.0, (v - mean) / sd)) for v in values]


@dataclass
class Runner:
    coin_id: str
    symbol: str
    price: float
    score: float
    reasons: list[str] = field(default_factory=list)


def latest_ticks(session: Session) -> list[CoinTick]:
    last = session.scalar(select(func.max(CoinTick.taken_at)))
    if last is None:
        return []
    return list(session.scalars(select(CoinTick).where(CoinTick.taken_at >= last - timedelta(minutes=1))))


def news_scores(session: Session, hours: int = 24) -> dict[str, float]:
    """Per coin symbol: importance-weighted bullish minus bearish news over the window."""
    since = utcnow() - timedelta(hours=hours)
    scores: dict[str, float] = defaultdict(float)
    rows = session.scalars(select(ItemAnalysis).where(
        ItemAnalysis.created_at >= since, ItemAnalysis.importance != "noise"))
    for a in rows:
        weight = IMPORTANCE_WEIGHT.get(a.importance, 0) * SENTIMENT_SIGN.get(a.sentiment, 0) * (a.confidence or 0.5)
        for coin in a.coins or []:
            scores[coin] += weight
    return scores


def volume_change(session: Session, hours: int = 6) -> dict[str, float]:
    """Per coin id: current 24h volume divided by the 24h volume `hours` ago."""
    target = utcnow() - timedelta(hours=hours)
    then_at = session.scalar(select(func.max(CoinTick.taken_at)).where(CoinTick.taken_at <= target))
    if then_at is None:
        return {}
    then = dict(session.execute(select(CoinTick.coin_id, CoinTick.volume_24h_usd).where(CoinTick.taken_at == then_at)).all())
    now = {t.coin_id: t.volume_24h_usd for t in latest_ticks(session)}
    return {cid: now[cid] / v for cid, v in then.items() if v and now.get(cid)}


def rank_runners(ticks: list[CoinTick], news: dict[str, float], vol_change: dict[str, float],
                 flagged: set[str], min_volume: float, top: int = 10) -> list[Runner]:
    """Momentum + turnover + volume acceleration + news, ranked against the rest of the market."""
    pool = [
        t for t in ticks
        if t.symbol.upper() not in EXCLUDED and t.price_usd and (t.volume_24h_usd or 0) >= min_volume
        and t.change_1h_pct is not None and t.change_24h_pct is not None and t.market_cap_usd
    ]
    if not pool:
        return []
    z1h = zscores([t.change_1h_pct for t in pool])
    z24 = zscores([max(-50.0, min(100.0, t.change_24h_pct)) for t in pool])
    zturn = zscores([t.volume_24h_usd / t.market_cap_usd for t in pool])
    zacc = zscores([vol_change.get(t.coin_id, 1.0) for t in pool])
    runners = []
    for i, t in enumerate(pool):
        n = news.get(t.symbol.upper(), 0.0)
        score = 0.30 * z1h[i] + 0.20 * z24[i] + 0.20 * zturn[i] + 0.15 * zacc[i] + 0.15 * max(-3, min(3, n * 2))
        reasons = []
        if z1h[i] > 1:
            reasons.append(f"+{t.change_1h_pct:.1f}% in 1h")
        if z24[i] > 1:
            reasons.append(f"+{t.change_24h_pct:.1f}% in 24h")
        if zturn[i] > 1:
            reasons.append("heavy trading for its size")
        if zacc[i] > 1:
            reasons.append(f"volume up {vol_change.get(t.coin_id, 1) - 1:.0%} in 6h")
        if n > 0.3:
            reasons.append("bullish news")
        if n < -0.3:
            reasons.append("bearish news")
        if t.symbol.upper() in flagged:
            score -= 1.0
            reasons.append("recent hack or risk alert")
        runners.append(Runner(t.coin_id, t.symbol.upper(), t.price_usd, round(score, 3), reasons))
    runners.sort(key=lambda r: r.score, reverse=True)
    return [r for r in runners[:top] if r.score > 0]


@dataclass
class MarketCall:
    direction: str
    score: float
    total_cap: float
    reasons: list[str]


def market_call(ticks: list[CoinTick], bullish_news: int, bearish_news: int, critical_alerts: int) -> MarketCall | None:
    cap = sum(t.market_cap_usd or 0 for t in ticks)
    if not cap:
        return None
    m1h = sum((t.market_cap_usd or 0) * (t.change_1h_pct or 0) for t in ticks) / cap
    m24 = sum((t.market_cap_usd or 0) * (t.change_24h_pct or 0) for t in ticks) / cap
    with_change = [t for t in ticks if t.change_24h_pct is not None]
    breadth = sum(t.change_24h_pct > 0 for t in with_change) / len(with_change) if with_change else 0.5
    news = (bullish_news - bearish_news) / max(5, bullish_news + bearish_news)

    parts = {
        "1h momentum": max(-1, min(1, m1h / 1.5)) * 0.25,
        "24h momentum": max(-1, min(1, m24 / 5)) * 0.25,
        "breadth": (breadth - 0.5) * 2 * 0.2,
        "news": news * 0.2,
        "hacks": -min(1, critical_alerts / 3) * 0.1,
    }
    score = round(sum(parts.values()), 3)
    direction = "bullish" if score >= 0.15 else "bearish" if score <= -0.15 else "neutral"
    reasons = [
        f"market {m24:+.1f}% 24h, {m1h:+.1f}% 1h",
        f"{breadth:.0%} of coins up",
        f"news {bullish_news} bullish / {bearish_news} bearish",
    ]
    if critical_alerts:
        reasons.append(f"{critical_alerts} critical alerts in 6h")
    return MarketCall(direction, score, cap, reasons)


def price_at(session: Session, coin_id: str, when) -> float | None:
    return session.scalar(
        select(CoinTick.price_usd).where(CoinTick.coin_id == coin_id, CoinTick.taken_at >= when)
        .order_by(CoinTick.taken_at).limit(1)
    )


def total_cap_at(session: Session, when) -> float | None:
    first = session.scalar(select(func.min(CoinTick.taken_at)).where(CoinTick.taken_at >= when))
    if first is None:
        return None
    return session.scalar(select(func.sum(CoinTick.market_cap_usd)).where(
        CoinTick.taken_at >= first, CoinTick.taken_at < first + timedelta(minutes=1)))


def resolve(session: Session) -> int:
    """Scores every prediction whose horizon has passed and that now has data to compare with."""
    now = utcnow()
    done = 0
    for p in session.scalars(select(Prediction).where(Prediction.resolved_at.is_(None))):
        created = p.created_at if p.created_at.tzinfo else p.created_at.replace(tzinfo=now.tzinfo)
        due = created + timedelta(hours=p.horizon_hours)
        if due > now or not p.start_value:
            continue
        cap_then = session.scalar(select(Prediction.start_value).where(
            Prediction.batch == p.batch, Prediction.kind == "market"))
        cap_now = total_cap_at(session, due)
        market_pct = (cap_now / cap_then - 1) * 100 if cap_now and cap_then else None
        if p.kind == "market":
            outcome = market_pct
            if outcome is None:
                continue
            p.hit = (outcome >= 1 if p.direction == "bullish" else outcome <= -1 if p.direction == "bearish"
                     else abs(outcome) < 1)
        else:
            end = price_at(session, p.subject, due)
            if end is None:
                continue
            outcome = (end / p.start_value - 1) * 100
            # A pick counts as a hit when it went up and did better than the market as a whole.
            p.hit = outcome > 0 and (market_pct is None or outcome > market_pct)
        p.outcome_pct, p.market_pct, p.resolved_at = round(outcome, 3), market_pct, now
        done += 1
    session.commit()
    return done


def track_record(session: Session, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    rows = session.scalars(select(Prediction).where(
        Prediction.resolved_at.is_not(None), Prediction.created_at >= since)).all()
    runners = [p for p in rows if p.kind == "runner"]
    calls = [p for p in rows if p.kind == "market"]
    avg = lambda xs: round(statistics.fmean(xs), 2) if xs else None  # noqa: E731
    return {
        "days": days,
        "runner_picks": len(runners),
        "runner_hit_rate": avg([float(p.hit) for p in runners]),
        "runner_avg_return_pct": avg([p.outcome_pct for p in runners]),
        "market_avg_return_pct": avg([p.market_pct for p in runners if p.market_pct is not None]),
        "market_calls": len(calls),
        "market_hit_rate": avg([float(p.hit) for p in calls]),
        "pending": session.scalar(select(func.count()).select_from(Prediction).where(Prediction.resolved_at.is_(None))),
    }


def dex_runners(session: Session, top: int = 10) -> list[dict]:
    """Trending DEX tokens that pass the liquidity and safety bar. Always high risk."""
    last = session.scalar(select(func.max(DexTick.taken_at)))
    if last is None:
        return []
    ticks = session.scalars(select(DexTick).where(DexTick.taken_at >= last - timedelta(minutes=1))).all()
    safety = {(s.chain, s.token_address): s for s in session.scalars(select(TokenSafety))}
    out = []
    for t in ticks:
        s = safety.get((t.chain, t.base_address))
        if (t.liquidity_usd or 0) < settings.dex_runner_min_liquidity or s is None or (s.risk_score or 0) >= 0.5:
            continue
        if t.change_24h_pct is None or t.change_24h_pct <= 0:
            continue
        turnover = (t.volume_24h_usd or 0) / t.liquidity_usd
        score = min(t.change_24h_pct, 300) / 100 + min(turnover, 10) / 5
        out.append({
            "symbol": t.base_symbol, "chain": t.chain, "price": t.price_usd, "liquidity": t.liquidity_usd,
            "volume": t.volume_24h_usd, "ch24h": t.change_24h_pct, "url": t.url, "score": round(score, 2),
            "risk": s.risk_score, "risks": s.risks,
        })
    # Several pairs can trade the same token; keep the best one.
    best: dict[tuple, dict] = {}
    for r in sorted(out, key=lambda r: r["score"], reverse=True):
        best.setdefault((r["chain"], r["symbol"]), r)
    return list(best.values())[:top]


def latest_signals(session: Session) -> dict:
    batch = session.scalar(select(func.max(Prediction.batch)))
    rows = session.scalars(select(Prediction).where(Prediction.batch == batch).order_by(Prediction.rank)).all() if batch else []
    market = next((p for p in rows if p.kind == "market"), None)
    return {
        "at": iso(rows[0].created_at) if rows else None,
        "market": None if market is None else {
            "direction": market.direction, "score": market.score, "reasons": market.reasons,
        },
        "runners": [
            {"symbol": p.symbol, "coin_id": p.subject, "score": p.score, "price": p.start_value, "reasons": p.reasons}
            for p in rows if p.kind == "runner"
        ],
        "dex": dex_runners(session),
        "record": track_record(session),
    }


class Signals(Job):
    """Every hour: a market call and the top runners, saved as predictions and scored 24h later."""

    name = "signals"
    interval_seconds = 3600

    async def run(self, client: httpx.AsyncClient, sessions: sessionmaker[Session]) -> tuple[int, int]:
        with sessions() as session:
            resolved = resolve(session)
            ticks = latest_ticks(session)
            if not ticks:
                return 0, resolved
            since = utcnow() - timedelta(hours=6)
            mood = dict(session.execute(
                select(ItemAnalysis.sentiment, func.count())
                .where(ItemAnalysis.created_at >= since, ItemAnalysis.importance != "noise")
                .group_by(ItemAnalysis.sentiment)).all())
            critical = session.scalar(select(func.count()).select_from(Alert).where(
                Alert.created_at >= since, Alert.severity == "critical", Alert.kind.in_(["hack", "major_news"]))) or 0
            flagged = {
                sym for (syms,) in session.execute(select(Alert.symbol).where(
                    Alert.created_at >= utcnow() - timedelta(hours=24), Alert.kind.in_(["hack", "rug_risk"])))
                for sym in (syms or "").split(",") if sym
            }
            call = market_call(ticks, mood.get("bullish", 0), mood.get("bearish", 0), critical)
            runners = rank_runners(ticks, news_scores(session), volume_change(session), flagged,
                                   settings.runner_min_volume)

            previous = session.scalar(select(Prediction).where(Prediction.kind == "market")
                                      .order_by(Prediction.id.desc()).limit(1))
            batch = (session.scalar(select(func.max(Prediction.batch))) or 0) + 1
            if call:
                session.add(Prediction(batch=batch, kind="market", subject="market", direction=call.direction,
                                       score=call.score, reasons=call.reasons, start_value=call.total_cap))
            for rank, r in enumerate(runners, 1):
                session.add(Prediction(batch=batch, kind="runner", subject=r.coin_id, symbol=r.symbol,
                                       direction="bullish", score=r.score, rank=rank, reasons=r.reasons,
                                       start_value=r.price))
            session.flush()

            now = utcnow()
            if call and previous and previous.direction != call.direction:
                add_alert(session, f"market_call:{batch}", kind="market_call", severity="info",
                          title=f"Market call flipped {previous.direction} -> {call.direction} ({call.score:+.2f})",
                          detail="; ".join(call.reasons))
            if runners and now.hour >= settings.daily_picks_hour_utc:
                picks = ", ".join(f"{r.symbol} ({'; '.join(r.reasons[:2]) or 'momentum'})" for r in runners[:5])
                add_alert(session, f"picks:{now:%Y-%m-%d}", kind="picks", severity="info",
                          title=f"Today's top runners: {', '.join(r.symbol for r in runners[:5])}",
                          detail=f"{picks}. Market: {call.direction if call else '?'}. Signals, not advice.")
            session.commit()
            return len(ticks), len(runners) + (1 if call else 0) + resolved
