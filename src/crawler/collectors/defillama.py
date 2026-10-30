import httpx

from ..records import NewsItem
from .base import Collector, from_unix, to_float


def parse_hacks(rows: list[dict]) -> list[NewsItem]:
    items = []
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        amount = to_float(row.get("amount"))
        title = f"{name} hacked"
        if amount:
            title += f" for ${amount / 1e6:,.1f}M"
        # Some entries have no source link, so fall back to a stable made-up key.
        url = row.get("source") or f"defillama://hacks/{name}/{row.get('date')}"
        items.append(NewsItem(
            source="defillama",
            kind="hack",
            title=title,
            url=url,
            published_at=from_unix(row.get("date")),
            summary=row.get("technique") or row.get("classification") or "",
            extra={
                "amount_usd": amount,
                "chains": row.get("chain") or [],
                "classification": row.get("classification"),
                "technique": row.get("technique"),
                "target_type": row.get("targetType"),
                "returned_usd": to_float(row.get("returnedFunds")),
            },
        ))
    return items


class DefiLlamaHacksCollector(Collector):
    """DeFiLlama's hack database: exploits, amounts stolen and chains hit."""

    name = "defillama_hacks"
    interval_seconds = 900

    async def collect(self, client: httpx.AsyncClient) -> list[NewsItem]:
        resp = await client.get("https://api.llama.fi/hacks")
        resp.raise_for_status()
        return parse_hacks(resp.json())
