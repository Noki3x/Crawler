"""Rug-pull and liquidity warnings for the DEX tokens the bot is watching."""
import logging
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings
from ..db import DexTick, Item, TokenSafety, add_alert, utcnow
from ..jobs import Job

log = logging.getLogger(__name__)

# DexScreener chain id -> GoPlus numeric chain id (EVM chains only).
GOPLUS_CHAINS = {
    "ethereum": "1", "bsc": "56", "base": "8453", "arbitrum": "42161",
    "polygon": "137", "avalanche": "43114", "optimism": "10",
}

GOPLUS_FLAGS = {
    "is_honeypot": ("Honeypot: you can buy but not sell", 1.0),
    "cannot_sell_all": ("Holders can't sell their whole balance", 0.6),
    "owner_change_balance": ("Owner can change holder balances", 0.6),
    "hidden_owner": ("Hidden owner", 0.5),
    "selfdestruct": ("Contract can self-destruct", 0.5),
    "is_blacklisted": ("Owner can blacklist wallets", 0.3),
    "is_mintable": ("Owner can mint more supply", 0.2),
    "is_proxy": ("Upgradeable proxy contract", 0.2),
}
RUGCHECK_WEIGHTS = {"danger": 0.5, "warn": 0.2}


def score_rugcheck(report: dict) -> tuple[float, list[str]]:
    risks = report.get("risks") or []
    score = sum(RUGCHECK_WEIGHTS.get(r.get("level", ""), 0) for r in risks)
    return min(score, 1.0), [r.get("name", "?") for r in risks if r.get("level") in RUGCHECK_WEIGHTS]


def score_goplus(data: dict) -> tuple[float, list[str]]:
    score, risks = 0.0, []
    for flag, (label, weight) in GOPLUS_FLAGS.items():
        if str(data.get(flag)) == "1":
            score += weight
            risks.append(label)
    if str(data.get("is_open_source")) == "0":
        score += 0.3
        risks.append("Contract source not verified")
    for side in ("buy_tax", "sell_tax"):
        try:
            tax = float(data.get(side) or 0)
        except ValueError:
            continue
        if tax >= 0.1:
            score += 0.5
            risks.append(f"{side.replace('_', ' ').title()} {tax:.0%}")
    return min(score, 1.0), risks


class TokenWatch(Job):
    """Flags thin liquidity, pulled liquidity, crashes, and contracts that RugCheck or GoPlus call risky."""

    name = "token_watch"
    interval_seconds = 120

    def __init__(self, low_liquidity_usd: float = settings.low_liquidity_usd, checks_per_run: int = 20):
        self.low_liquidity_usd = low_liquidity_usd
        self.checks_per_run = checks_per_run

    def market_alerts(self, session: Session) -> int:
        """Rules on DexScreener snapshots: thin liquidity, liquidity pulled, price crash."""
        now = utcnow()
        latest_at = session.scalar(select(func.max(DexTick.taken_at)))
        if latest_at is None:
            return 0
        latest = session.scalars(select(DexTick).where(DexTick.taken_at == latest_at)).all()
        day = now.strftime("%Y-%m-%d")
        created = 0
        for t in latest:
            label = f"{t.base_symbol} ({t.chain})"
            if t.liquidity_usd is not None and t.liquidity_usd < self.low_liquidity_usd:
                created += add_alert(
                    session, f"lowliq:{t.pair_address}:{day}", kind="low_liquidity", severity="warn",
                    title=f"{label} has only ${t.liquidity_usd:,.0f} liquidity",
                    detail=f"24h volume ${t.volume_24h_usd or 0:,.0f}. Large sells will move the price hard.",
                    url=t.url, symbol=t.base_symbol, chain=t.chain, token_address=t.base_address,
                )
            peak = session.scalar(
                select(func.max(DexTick.liquidity_usd))
                .where(DexTick.pair_address == t.pair_address, DexTick.taken_at >= now - timedelta(hours=6))
            )
            if peak and t.liquidity_usd is not None and peak >= 10_000 and t.liquidity_usd <= peak * 0.5:
                created += add_alert(
                    session, f"liqpull:{t.pair_address}:{day}", kind="rug_risk", severity="critical",
                    title=f"{label} liquidity fell {1 - t.liquidity_usd / peak:.0%} in 6h",
                    detail=f"${peak:,.0f} -> ${t.liquidity_usd:,.0f}. Possible rug pull.",
                    url=t.url, symbol=t.base_symbol, chain=t.chain, token_address=t.base_address,
                )
            if t.change_24h_pct is not None and t.change_24h_pct <= -70:
                created += add_alert(
                    session, f"crash:{t.pair_address}:{day}", kind="crash", severity="warn",
                    title=f"{label} down {abs(t.change_24h_pct):.0f}% in 24h",
                    url=t.url, symbol=t.base_symbol, chain=t.chain, token_address=t.base_address,
                )
        session.commit()
        return created

    async def check_token(self, client: httpx.AsyncClient, chain: str, address: str) -> tuple[str, float, list[str]] | None:
        if chain == "solana":
            resp = await client.get(f"https://api.rugcheck.xyz/v1/tokens/{address}/report/summary")
            resp.raise_for_status()
            return ("rugcheck", *score_rugcheck(resp.json()))
        if chain in GOPLUS_CHAINS:
            resp = await client.get(
                f"https://api.gopluslabs.io/api/v1/token_security/{GOPLUS_CHAINS[chain]}",
                params={"contract_addresses": address},
            )
            resp.raise_for_status()
            result = (resp.json().get("result") or {}).get(address.lower())
            return ("goplus", *score_goplus(result)) if result else None
        return None

    async def safety_checks(self, client: httpx.AsyncClient, session: Session) -> int:
        cutoff = utcnow() - timedelta(hours=24)
        recent = utcnow() - timedelta(hours=1)
        checked = {
            (c, a) for c, a in session.execute(
                select(TokenSafety.chain, TokenSafety.token_address).where(TokenSafety.checked_at >= cutoff)
            )
        }
        candidates = session.execute(
            select(DexTick.chain, DexTick.base_address, func.max(DexTick.base_symbol), func.max(DexTick.url))
            .where(DexTick.taken_at >= recent)
            .group_by(DexTick.chain, DexTick.base_address)
        ).all()
        created = done = 0
        for chain, address, symbol, url in candidates:
            if (chain, address) in checked or done >= self.checks_per_run:
                continue
            try:
                verdict = await self.check_token(client, chain, address)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("safety check %s %s failed: %s", chain, address, exc)
                continue
            if verdict is None:
                continue
            done += 1
            provider, score, risks = verdict
            row = session.scalar(select(TokenSafety).where(
                TokenSafety.chain == chain, TokenSafety.token_address == address))
            if row is None:
                row = TokenSafety(chain=chain, token_address=address, provider=provider)
                session.add(row)
            row.risk_score, row.risks, row.checked_at = score, risks, utcnow()
            if score >= 0.5:
                created += add_alert(
                    session, f"rugcheck:{chain}:{address}", kind="rug_risk",
                    severity="critical" if score >= 0.8 else "warn",
                    title=f"{symbol} ({chain}) flagged as risky by {provider}",
                    detail="; ".join(risks), url=url, symbol=symbol, chain=chain, token_address=address,
                )
            session.commit()  # don't hold the SQLite write lock across the next HTTP call
        return created

    async def run(self, client: httpx.AsyncClient, sessions: sessionmaker[Session]) -> tuple[int, int]:
        with sessions() as session:
            created = self.market_alerts(session)
            created += await self.safety_checks(client, session)
        return 0, created


class HackAlerts(Job):
    """Turns hack reports from the collectors into alerts straight away, without waiting for Claude."""

    name = "hack_alerts"
    interval_seconds = 120

    async def run(self, client: httpx.AsyncClient, sessions: sessionmaker[Session]) -> tuple[int, int]:
        cutoff = utcnow() - timedelta(days=3)
        created = 0
        with sessions() as session:
            hacks = session.scalars(select(Item).where(
                Item.kind == "hack", Item.published_at >= cutoff)).all()
            for h in hacks:
                amount = (h.extra or {}).get("amount_usd") or 0
                created += add_alert(
                    session, f"item:{h.id}", kind="hack",
                    severity="critical" if amount >= 10_000_000 else "warn",
                    title=h.title, detail=h.summary[:300], url=h.url,
                )
            session.commit()
        return len(hacks), created
