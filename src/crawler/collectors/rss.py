import calendar
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

import feedparser
import httpx

from ..records import NewsItem
from .base import Collector

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Feed:
    source: str
    kind: str
    url: str


FEEDS = [
    Feed("coindesk", "news", "https://www.coindesk.com/arc/outboundfeeds/rss"),
    Feed("cointelegraph", "news", "https://cointelegraph.com/rss"),
    Feed("decrypt", "news", "https://decrypt.co/feed"),
    Feed("theblock", "news", "https://www.theblock.co/rss.xml"),
    Feed("bitcoinmagazine", "news", "https://bitcoinmagazine.com/feed"),
    Feed("cryptoslate", "news", "https://cryptoslate.com/feed/"),
    Feed("rekt", "hack", "https://rekt.news/rss/feed.xml"),
    Feed("sec", "regulatory", "https://www.sec.gov/news/pressreleases.rss"),
    Feed("cftc", "regulatory", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
]


def parse_feed(feed: Feed, body: bytes) -> list[NewsItem]:
    parsed = feedparser.parse(body)
    items = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        published = (
            datetime.fromtimestamp(calendar.timegm(stamp), tz=timezone.utc) if stamp else None
        )
        items.append(NewsItem(
            source=feed.source,
            kind=feed.kind,
            title=title.strip(),
            url=link.strip(),
            published_at=published,
            summary=entry.get("summary", "")[:2000],
        ))
    return items


class RssCollector(Collector):
    name = "rss"
    interval_seconds = 120

    def __init__(self, feeds: list[Feed] = FEEDS):
        self.feeds = feeds

    async def collect(self, client: httpx.AsyncClient) -> list[NewsItem]:
        items: list[NewsItem] = []
        failed = []
        for feed in self.feeds:
            # One bad feed shouldn't stop the others.
            try:
                resp = await client.get(feed.url)
                resp.raise_for_status()
                items.extend(parse_feed(feed, resp.content))
            except httpx.HTTPError as exc:
                log.warning("feed %s failed: %s", feed.source, exc)
                failed.append(feed.source)
        if failed and len(failed) == len(self.feeds):
            raise RuntimeError("every feed failed")
        return items
