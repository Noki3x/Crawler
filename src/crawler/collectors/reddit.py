import logging
import time

import httpx

from ..records import NewsItem
from .base import Collector, from_unix

log = logging.getLogger(__name__)

SUBREDDITS = ["CryptoCurrency", "CryptoMarkets", "Bitcoin", "ethereum", "solana", "SatoshiStreetBets"]
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_URL = "https://oauth.reddit.com"


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
    """Reads new posts through Reddit's official API with an app-only OAuth token."""

    name = "reddit"
    interval_seconds = 180

    def __init__(self, client_id: str, client_secret: str, subreddits: list[str] = SUBREDDITS):
        self.client_id = client_id
        self.client_secret = client_secret
        self.subreddits = subreddits
        self._token: str | None = None
        self._token_expires = 0.0

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        resp = await client.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        # Refresh a minute early so a cycle never starts with a token about to lapse.
        self._token_expires = time.monotonic() + body.get("expires_in", 3600) - 60
        return self._token

    async def collect(self, client: httpx.AsyncClient) -> list[NewsItem]:
        token = await self._get_token(client)
        items: list[NewsItem] = []
        failed = []
        for sub in self.subreddits:
            try:
                resp = await client.get(
                    f"{API_URL}/r/{sub}/new",
                    params={"limit": 50, "raw_json": 1},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                items.extend(parse_listing(resp.json()))
            except httpx.HTTPError as exc:
                log.warning("r/%s failed: %s", sub, exc)
                failed.append(sub)
        if failed and len(failed) == len(self.subreddits):
            self._token = None  # a revoked or expired token fails everything; fetch a fresh one next cycle
            raise RuntimeError("every subreddit failed")
        return items
