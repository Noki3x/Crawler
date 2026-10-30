import httpx
import respx

from crawler.collectors.rss import Feed, RssCollector
from crawler.db import CollectorRun, Item, make_session_factory
from crawler.runner import make_client, run_once

from .test_parsers import RSS


@respx.mock
async def test_run_once_stores_and_dedupes(tmp_path):
    sessions = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    respx.get("https://example.com/rss").mock(return_value=httpx.Response(200, content=RSS))
    collector = RssCollector([Feed("x", "news", "https://example.com/rss")])

    async with make_client() as client:
        first = await run_once(collector, client, sessions)
        second = await run_once(collector, client, sessions)

    assert (first.fetched, first.stored) == (1, 1)
    assert (second.fetched, second.stored) == (1, 0)
    with sessions() as s:
        assert s.query(Item).count() == 1
        assert s.query(CollectorRun).count() == 2


@respx.mock
async def test_run_once_records_errors(tmp_path):
    sessions = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    respx.get("https://example.com/rss").mock(return_value=httpx.Response(503))
    collector = RssCollector([Feed("x", "news", "https://example.com/rss")])

    async with make_client() as client:
        run = await run_once(collector, client, sessions)

    assert run.error == "RuntimeError: every feed failed"


@respx.mock
async def test_reddit_uses_oauth_token():
    from crawler.collectors.reddit import RedditCollector

    token = respx.post("https://www.reddit.com/api/v1/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
    )
    listing = respx.get("https://oauth.reddit.com/r/solana/new").mock(return_value=httpx.Response(200, json={
        "data": {"children": [{"data": {"title": "SOL", "permalink": "/r/solana/comments/1/x/", "subreddit": "solana"}}]}
    }))
    collector = RedditCollector("id", "secret", ["solana"])

    async with make_client() as client:
        first = await collector.collect(client)
        await collector.collect(client)

    assert [i.title for i in first] == ["SOL"]
    assert token.call_count == 1
    assert listing.calls.last.request.headers["Authorization"] == "Bearer tok"
