from datetime import timedelta
from types import SimpleNamespace

from crawler.analysis.classifier import Classifier, Verdict, Verdicts
from crawler.analysis.tokens import TokenWatch, score_goplus, score_rugcheck
from crawler.db import Alert, DexTick, Item, ItemAnalysis, make_session_factory, utcnow
from crawler.runner import make_client


def make_db(tmp_path):
    return make_session_factory(f"sqlite:///{tmp_path}/t.db")


def test_score_rugcheck():
    score, risks = score_rugcheck({"risks": [
        {"name": "Mint authority still enabled", "level": "danger"},
        {"name": "Low liquidity", "level": "warn"},
        {"name": "Fine", "level": "info"},
    ]})
    assert score == 0.7
    assert risks == ["Mint authority still enabled", "Low liquidity"]


def test_score_goplus_honeypot_and_tax():
    score, risks = score_goplus({"is_honeypot": "1", "sell_tax": "0.25", "is_open_source": "1"})
    assert score == 1.0
    assert "Sell Tax 25%" in risks


def test_score_goplus_clean():
    assert score_goplus({"is_honeypot": "0", "is_open_source": "1", "sell_tax": "0"}) == (0.0, [])


class FakeMessages:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=Verdicts(verdicts=self.verdicts))


async def test_classifier_tags_items_and_raises_alerts(tmp_path):
    sessions = make_db(tmp_path)
    with sessions() as s:
        s.add_all([
            Item(url_hash="a", source="coindesk", kind="news", title="SEC approves SOL ETF", url="https://x/a"),
            Item(url_hash="b", source="coindesk", kind="news", title="5 memecoins to watch", url="https://x/b"),
        ])
        s.commit()
        ids = [i.id for i in s.query(Item).order_by(Item.id)]

    messages = FakeMessages([
        Verdict(id=ids[0], importance="major", sentiment="bullish", category="etf",
                coins=["sol", "$SOL"], summary="SEC approved a spot SOL ETF.", confidence=0.9),
        Verdict(id=ids[1], importance="noise", sentiment="neutral", category="other",
                coins=[], summary="Listicle.", confidence=0.8),
    ])
    classifier = Classifier(client=SimpleNamespace(beta=SimpleNamespace(messages=messages)), model="test")

    async with make_client() as client:
        seen, stored = await classifier.run(client, sessions)

    assert (seen, stored) == (2, 2)
    assert len(messages.calls) == 1
    with sessions() as s:
        major = s.query(ItemAnalysis).filter_by(item_id=ids[0]).one()
        assert major.coins == ["SOL"]
        [alert] = s.query(Alert).all()
        assert alert.kind == "major_news" and alert.severity == "critical"
        assert s.query(Item).filter(Item.analysed_at.is_(None)).count() == 0

    # Nothing left to analyse, so no second API call.
    async with make_client() as client:
        assert await classifier.run(client, sessions) == (0, 0)
    assert len(messages.calls) == 1


def tick(**kw):
    base = dict(chain="solana", pair_address="p1", dex="raydium", base_symbol="RUG", base_address="t1",
                quote_symbol="SOL", price_usd=1.0, liquidity_usd=100_000, volume_24h_usd=50_000,
                change_24h_pct=0.0, pair_created_at=None, url="https://dexscreener.com/solana/p1")
    base.update(kw)
    return DexTick(**base)


def test_market_alerts_flag_pulled_liquidity_thin_pools_and_crashes(tmp_path):
    sessions = make_db(tmp_path)
    now = utcnow()
    with sessions() as s:
        s.add(tick(taken_at=now - timedelta(hours=2), liquidity_usd=200_000))
        s.add(tick(taken_at=now, liquidity_usd=20_000, change_24h_pct=-85))
        s.add(tick(taken_at=now, pair_address="p2", base_symbol="OK", base_address="t2"))
        s.commit()
        created = TokenWatch(low_liquidity_usd=25_000).market_alerts(s)
        kinds = sorted(a.kind for a in s.query(Alert))

    assert created == 3
    assert kinds == ["crash", "low_liquidity", "rug_risk"]

    # Same conditions on the next run don't repeat the alerts.
    with sessions() as s:
        assert TokenWatch(low_liquidity_usd=25_000).market_alerts(s) == 0


async def test_safety_checks_flag_risky_tokens_once(tmp_path):
    import httpx
    import respx

    sessions = make_db(tmp_path)
    with sessions() as s:
        s.add(tick(taken_at=utcnow()))
        s.commit()

    with respx.mock:
        route = respx.get("https://api.rugcheck.xyz/v1/tokens/t1/report/summary").mock(
            return_value=httpx.Response(200, json={"risks": [
                {"name": "Freeze authority enabled", "level": "danger"},
                {"name": "Top 10 holders own 90%", "level": "danger"},
            ]}))
        async with make_client() as client:
            with sessions() as s:
                assert await TokenWatch().safety_checks(client, s) == 1
            with sessions() as s:
                assert await TokenWatch().safety_checks(client, s) == 0
        assert route.call_count == 1

    with sessions() as s:
        [alert] = s.query(Alert).all()
        assert alert.severity == "critical"
        assert "Freeze authority" in alert.detail
