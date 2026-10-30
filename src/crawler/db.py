import hashlib
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .records import CoinSnapshot, DexPairSnapshot, NewsItem, Record


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # SQLite hands back naive datetimes; everything is stored in UTC.
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


class Base(DeclarativeBase):
    pass


class Item(Base):
    """Anything worth reading: articles, posts, hack reports, regulator notices."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("url_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url_hash: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # Filled in by the AI analysis step (step 2).
    analysed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CoinTick(Base):
    __tablename__ = "coin_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    coin_id: Mapped[str] = mapped_column(String(128), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    price_usd: Mapped[float | None] = mapped_column(Float)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    volume_24h_usd: Mapped[float | None] = mapped_column(Float)
    change_1h_pct: Mapped[float | None] = mapped_column(Float)
    change_24h_pct: Mapped[float | None] = mapped_column(Float)
    change_7d_pct: Mapped[float | None] = mapped_column(Float)


class DexTick(Base):
    __tablename__ = "dex_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    chain: Mapped[str] = mapped_column(String(32), index=True)
    pair_address: Mapped[str] = mapped_column(String(128), index=True)
    dex: Mapped[str] = mapped_column(String(64))
    base_symbol: Mapped[str] = mapped_column(String(64))
    base_address: Mapped[str] = mapped_column(String(128), index=True)
    quote_symbol: Mapped[str] = mapped_column(String(64))
    price_usd: Mapped[float | None] = mapped_column(Float)
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    volume_24h_usd: Mapped[float | None] = mapped_column(Float)
    change_24h_pct: Mapped[float | None] = mapped_column(Float)
    pair_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str] = mapped_column(Text)


class CollectorRun(Base):
    """One row per collector run, so the terminal can show what the bot is doing."""

    __tablename__ = "collector_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collector: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    stored: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class ItemAnalysis(Base):
    """Claude's read of one item: how big it is, which way it cuts, and which coins it touches."""

    __tablename__ = "item_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    importance: Mapped[str] = mapped_column(String(8), index=True)  # major | minor | noise
    sentiment: Mapped[str] = mapped_column(String(8), index=True)  # bullish | bearish | neutral
    category: Mapped[str] = mapped_column(String(24), index=True)
    coins: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Alert(Base):
    """Something worth interrupting you for: major news, hacks, rug risks, thin liquidity."""

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("dedupe_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    severity: Mapped[str] = mapped_column(String(8), index=True)  # info | warn | critical
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    symbol: Mapped[str] = mapped_column(String(64), default="")
    chain: Mapped[str] = mapped_column(String(32), default="")
    token_address: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # Set once the alert has been pushed to your phone (step 4).
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TokenSafety(Base):
    """Cached RugCheck / GoPlus verdicts so each token is checked at most once a day."""

    __tablename__ = "token_safety"
    __table_args__ = (UniqueConstraint("chain", "token_address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain: Mapped[str] = mapped_column(String(32))
    token_address: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(16))
    risk_score: Mapped[float | None] = mapped_column(Float)  # 0 safe .. 1 dangerous
    risks: Mapped[list] = mapped_column(JSON, default=list)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Wallet(Base):
    """A wallet you own, tracked by its public address only. Never store keys or seed phrases."""

    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("chain", "address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(64))
    chain: Mapped[str] = mapped_column(String(16))  # solana | ethereum | base | bitcoin ...
    address: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WalletBalance(Base):
    """One asset in one wallet at one moment."""

    __tablename__ = "wallet_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(Integer, index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    symbol: Mapped[str] = mapped_column(String(64))
    token_address: Mapped[str] = mapped_column(String(128), default="")
    amount: Mapped[float] = mapped_column(Float)
    value_usd: Mapped[float | None] = mapped_column(Float)


class Prediction(Base):
    """A call the bot made, scored against what actually happened once its horizon passes."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # market | runner
    subject: Mapped[str] = mapped_column(String(128))  # "market" or a CoinGecko id
    symbol: Mapped[str] = mapped_column(String(32), default="")
    direction: Mapped[str] = mapped_column(String(8))  # bullish | bearish | neutral
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    start_value: Mapped[float | None] = mapped_column(Float)  # price, or total market cap for market calls
    horizon_hours: Mapped[int] = mapped_column(Integer, default=24)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    outcome_pct: Mapped[float | None] = mapped_column(Float)
    market_pct: Mapped[float | None] = mapped_column(Float)
    hit: Mapped[bool | None] = mapped_column()


def add_alert(session: Session, dedupe_key: str, **fields) -> bool:
    """Insert an alert unless one with the same key exists. Returns True if it was new."""
    if session.scalar(select(Alert.id).where(Alert.dedupe_key == dedupe_key)):
        return False
    session.add(Alert(dedupe_key=dedupe_key[:200], **fields))
    session.flush()
    return True


def make_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()


def store(session: Session, records: list[Record]) -> int:
    """Save records and return how many were new. News is de-duplicated by URL."""
    stored = 0
    news = [r for r in records if isinstance(r, NewsItem)]
    if news:
        hashes = {url_hash(n.url): n for n in news}
        seen = set(session.scalars(select(Item.url_hash).where(Item.url_hash.in_(hashes))))
        for h, n in hashes.items():
            if h in seen:
                continue
            session.add(Item(
                url_hash=h, source=n.source, kind=n.kind, title=n.title, url=n.url,
                summary=n.summary, extra=n.extra, published_at=n.published_at,
            ))
            stored += 1
    for r in records:
        if isinstance(r, CoinSnapshot):
            session.add(CoinTick(**vars(r)))
            stored += 1
        elif isinstance(r, DexPairSnapshot):
            session.add(DexTick(**vars(r)))
            stored += 1
    session.commit()
    return stored
