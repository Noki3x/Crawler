from .base import Collector
from .coingecko import CoinGeckoCollector
from .defillama import DefiLlamaHacksCollector
from .dexscreener import DexScreenerCollector
from .reddit import RedditCollector
from .rss import RssCollector


def all_collectors() -> list[Collector]:
    return [
        RssCollector(),
        RedditCollector(),
        CoinGeckoCollector(),
        DexScreenerCollector(),
        DefiLlamaHacksCollector(),
    ]
