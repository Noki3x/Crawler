import logging

import httpx

from ..records import NewsItem
from .base import Collector, from_unix

log = logging.getLogger(__name__)

SUBREDDITS = ["CryptoCurrency", "CryptoMarkets", "Bitcoin", "ethereum", "solana", "SatoshiStreetBets"]


def parse_listing(data: dict) -> list[NewsItem]:
    items = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        if not post.get("title") or not post.get("permalink"):
            continue
        items.append(NewsItem(
            source=f"reddit/r/{post.get('subreddit', '?')}",
            kind="social",
            title=post["title"].strip(),
            url="https://www.reddit.com" + post["permalink"],
            published_at=from_unix(post.get("created_utc")),
            summary=(post.get("selftext") or "")[:2000],
            extra={
                "score": post.get("score"),
                "comments": post.get("num_comments"),
                "link": post.get("url_overridden_by_dest"),
            },
        ))
    return items


class RedditCollector(Collector):
    name = "reddit"
    interval_seconds = 180

    def __init__(self, subreddits: list[str] = SUBREDDITS):
        self.subreddits = subreddits

    async def collect(self, client: httpx.AsyncClient) -> list[NewsItem]:
        items: list[NewsItem] = []
        failed = []
        for sub in self.subreddits:
            try:
                resp = await client.get(f"https://www.reddit.com/r/{sub}/new.json", params={"limit": 50})
                resp.raise_for_status()
                items.extend(parse_listing(resp.json()))
            except httpx.HTTPError as exc:
                log.warning("r/%s failed: %s", sub, exc)
                failed.append(sub)
        if failed and len(failed) == len(self.subreddits):
            raise RuntimeError("every subreddit failed")
        return items
