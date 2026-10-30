import httpx

from ..config import settings
from ..records import CoinSnapshot
from .base import Collector, to_float

BASE_URL = "https://api.coingecko.com/api/v3"


def parse_markets(rows: list[dict]) -> list[CoinSnapshot]:
    return [
        CoinSnapshot(
            coin_id=row["id"],
            symbol=(row.get("symbol") or "").upper(),
            name=row.get("name") or row["id"],
            price_usd=to_float(row.get("current_price")),
            market_cap_usd=to_float(row.get("market_cap")),
            volume_24h_usd=to_float(row.get("total_volume")),
            change_1h_pct=to_float(row.get("price_change_percentage_1h_in_currency")),
            change_24h_pct=to_float(
                row.get("price_change_percentage_24h_in_currency", row.get("price_change_percentage_24h"))
            ),
            change_7d_pct=to_float(row.get("price_change_percentage_7d_in_currency")),
        )
        for row in rows
        if row.get("id")
    ]


class CoinGeckoCollector(Collector):
    """Prices, volume and momentum for the top coins by market cap."""

    name = "coingecko"
    interval_seconds = 120

    def __init__(self, top_n: int = settings.coingecko_top_n, api_key: str = settings.coingecko_api_key):
        self.top_n = top_n
        self.headers = {"x-cg-demo-api-key": api_key} if api_key else {}

    async def collect(self, client: httpx.AsyncClient) -> list[CoinSnapshot]:
        snapshots: list[CoinSnapshot] = []
        page = 1
        while len(snapshots) < self.top_n:
            per_page = min(250, self.top_n - len(snapshots))
            resp = await client.get(f"{BASE_URL}/coins/markets", headers=self.headers, params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "price_change_percentage": "1h,24h,7d",
            })
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                break
            snapshots.extend(parse_markets(rows))
            page += 1
        return snapshots
