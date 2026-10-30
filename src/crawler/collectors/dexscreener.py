import httpx

from ..records import DexPairSnapshot
from .base import Collector, from_unix, to_float

BASE_URL = "https://api.dexscreener.com"
BATCH = 30  # max addresses per /tokens/v1 call


def parse_pairs(pairs: list[dict]) -> list[DexPairSnapshot]:
    out = []
    for p in pairs:
        base = p.get("baseToken") or {}
        if not p.get("pairAddress") or not base.get("address"):
            continue
        out.append(DexPairSnapshot(
            chain=p.get("chainId", ""),
            pair_address=p["pairAddress"],
            dex=p.get("dexId", ""),
            base_symbol=base.get("symbol", ""),
            base_address=base["address"],
            quote_symbol=(p.get("quoteToken") or {}).get("symbol", ""),
            price_usd=to_float(p.get("priceUsd")),
            liquidity_usd=to_float((p.get("liquidity") or {}).get("usd")),
            volume_24h_usd=to_float((p.get("volume") or {}).get("h24")),
            change_24h_pct=to_float((p.get("priceChange") or {}).get("h24")),
            pair_created_at=from_unix(p.get("pairCreatedAt")),
            url=p.get("url", ""),
        ))
    return out


class DexScreenerCollector(Collector):
    """Trending and newly promoted DEX tokens: where pumps, rugs and thin liquidity show up first."""

    name = "dexscreener"
    interval_seconds = 120

    async def _token_list(self, client: httpx.AsyncClient, path: str) -> list[tuple[str, str]]:
        resp = await client.get(f"{BASE_URL}{path}")
        resp.raise_for_status()
        return [(t["chainId"], t["tokenAddress"]) for t in resp.json() if t.get("tokenAddress")]

    async def collect(self, client: httpx.AsyncClient) -> list[DexPairSnapshot]:
        tokens = await self._token_list(client, "/token-boosts/top/v1")
        tokens += await self._token_list(client, "/token-profiles/latest/v1")

        by_chain: dict[str, list[str]] = {}
        for chain, address in dict.fromkeys(tokens):
            by_chain.setdefault(chain, []).append(address)

        snapshots: list[DexPairSnapshot] = []
        for chain, addresses in by_chain.items():
            for i in range(0, len(addresses), BATCH):
                batch = ",".join(addresses[i:i + BATCH])
                resp = await client.get(f"{BASE_URL}/tokens/v1/{chain}/{batch}")
                resp.raise_for_status()
                snapshots.extend(parse_pairs(resp.json()))
        return snapshots
