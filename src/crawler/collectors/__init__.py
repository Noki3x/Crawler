import logging

from ..config import settings
from .base import Collector
from .coingecko import CoinGeckoCollector
from .defillama import DefiLlamaHacksCollector
from .dexscreener import DexScreenerCollector
from .reddit import RedditCollector
from .rss import RssCollector

log = logging.getLogger(__name__)


def all_collectors() -> list[Collector]:
    collectors: list[Collector] = [
        RssCollector(),
        CoinGeckoCollector(),
        DexScreenerCollector(),
        DefiLlamaHacksCollector(),
    ]
    # Reddit blocks anonymous requests from servers, so it only runs with API credentials.
    if settings.reddit_client_id and settings.reddit_client_secret:
        collectors.insert(1, RedditCollector(settings.reddit_client_id, settings.reddit_client_secret))
    else:
        log.info("reddit skipped: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET to enable it")
    return collectors
