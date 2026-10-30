"""Parser tests on small hand-written payloads shaped like each source's real responses."""
from crawler.collectors.coingecko import parse_markets
from crawler.collectors.defillama import parse_hacks
from crawler.collectors.dexscreener import parse_pairs
from crawler.collectors.reddit import parse_listing
from crawler.collectors.rss import Feed, parse_feed

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
<item><title>Bitcoin tops $120k</title><link>https://example.com/a</link>
<pubDate>Thu, 30 Oct 2026 12:00:00 GMT</pubDate><description>Big day</description></item>
<item><title>No link here</title></item>
</channel></rss>"""


def test_parse_feed():
    items = parse_feed(Feed("x", "news", "https://example.com/rss"), RSS)
    assert len(items) == 1
    assert items[0].title == "Bitcoin tops $120k"
    assert items[0].published_at.isoformat() == "2026-10-30T12:00:00+00:00"
    assert items[0].kind == "news"


def test_parse_reddit_listing():
    data = {"data": {"children": [{"data": {
        "title": "SOL pumping", "permalink": "/r/solana/comments/1/x/", "subreddit": "solana",
        "created_utc": 1793361600, "score": 42, "num_comments": 7, "selftext": "body",
    }}]}}
    [item] = parse_listing(data)
    assert item.url == "https://www.reddit.com/r/solana/comments/1/x/"
    assert item.source == "reddit/r/solana"
    assert item.extra["score"] == 42


def test_parse_markets():
    [snap] = parse_markets([{
        "id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 120000,
        "market_cap": 2.4e12, "total_volume": 5e10,
        "price_change_percentage_1h_in_currency": 0.5,
        "price_change_percentage_24h": 3.1,
        "price_change_percentage_7d_in_currency": None,
    }])
    assert snap.symbol == "BTC"
    assert snap.change_24h_pct == 3.1
    assert snap.change_7d_pct is None


def test_parse_pairs():
    [pair] = parse_pairs([{
        "chainId": "solana", "dexId": "raydium", "url": "https://dexscreener.com/solana/p",
        "pairAddress": "p", "baseToken": {"address": "t", "symbol": "WIF"},
        "quoteToken": {"symbol": "SOL"}, "priceUsd": "1.23",
        "liquidity": {"usd": 5000}, "volume": {"h24": 100000}, "priceChange": {"h24": -40},
        "pairCreatedAt": 1793361600000,
    }, {"chainId": "solana"}])
    assert pair.price_usd == 1.23
    assert pair.liquidity_usd == 5000
    assert pair.pair_created_at.year == 2026


def test_parse_hacks():
    items = parse_hacks([
        {"name": "SomeDEX", "date": 1793361600, "amount": 12_500_000, "chain": ["Ethereum"],
         "technique": "Private key compromised", "source": "https://example.com/hack"},
        {"name": "NoSource", "date": 1793361600},
    ])
    assert items[0].title == "SomeDEX hacked for $12.5M"
    assert items[0].extra["chains"] == ["Ethereum"]
    assert items[1].url.startswith("defillama://")
