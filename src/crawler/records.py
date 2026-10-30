"""Plain records that collectors return. The runner turns them into rows."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    source: str
    kind: str  # news | social | hack | regulatory
    title: str
    url: str
    published_at: datetime | None = None
    summary: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class CoinSnapshot:
    coin_id: str
    symbol: str
    name: str
    price_usd: float | None
    market_cap_usd: float | None
    volume_24h_usd: float | None
    change_1h_pct: float | None
    change_24h_pct: float | None
    change_7d_pct: float | None


@dataclass
class DexPairSnapshot:
    chain: str
    pair_address: str
    dex: str
    base_symbol: str
    base_address: str
    quote_symbol: str
    price_usd: float | None
    liquidity_usd: float | None
    volume_24h_usd: float | None
    change_24h_pct: float | None
    pair_created_at: datetime | None
    url: str


Record = NewsItem | CoinSnapshot | DexPairSnapshot
