from datetime import timedelta

import httpx
import respx

from crawler.db import Alert, CoinTick, Prediction, make_session_factory, utcnow
from crawler.notify import Notifier, should_send
from crawler.runner import make_client
from crawler.signals import Signals, latest_signals, market_call, rank_runners, resolve


def coin(cid, sym, price=1.0, ch1h=0.0, ch24h=0.0, vol=1e8, cap=1e9, at=None):
    return CoinTick(coin_id=cid, symbol=sym, name=sym, price_usd=price, market_cap_usd=cap, volume_24h_usd=vol,
                    change_1h_pct=ch1h, change_24h_pct=ch24h, change_7d_pct=0.0, taken_at=at or utcnow())


def test_rank_runners_prefers_momentum_and_skips_stables():
    ticks = [coin("a", "AAA", ch1h=0.1), coin("b", "BBB", ch1h=0.2), coin("c", "CCC", ch1h=-0.3),
             coin("hot", "HOT", ch1h=6.0, ch24h=25.0, vol=8e8),
             coin("tether", "USDT", ch1h=9.0, ch24h=40.0, vol=9e10)]
    runners = rank_runners(ticks, news={}, vol_change={}, flagged=set(), min_volume=1e6)
    assert runners[0].symbol == "HOT"
    assert "USDT" not in {r.symbol for r in runners}
    assert any("1h" in reason for reason in runners[0].reasons)


def test_rank_runners_penalises_flagged_coins():
    ticks = [coin("x", "X"), coin("y", "Y"), coin("hot", "HOT", ch1h=6.0, ch24h=25.0, vol=8e8)]
    clean = rank_runners(ticks, {}, {}, set(), 1e6)
    flagged = rank_runners(ticks, {}, {}, {"HOT"}, 1e6)
    assert clean[0].symbol == "HOT"
    # The penalty knocks it down or out of the list (only positive scores are kept).
    assert all(r.symbol != "HOT" or r.score == round(clean[0].score - 1.0, 3) for r in flagged)
    assert not flagged or flagged[0].score < clean[0].score


def test_market_call_direction():
    up = [coin("a", "A", ch1h=1.0, ch24h=6.0), coin("b", "B", ch1h=0.8, ch24h=4.0)]
    down = [coin("a", "A", ch1h=-1.0, ch24h=-6.0), coin("b", "B", ch1h=-0.8, ch24h=-4.0)]
    assert market_call(up, 8, 1, 0).direction == "bullish"
    assert market_call(down, 1, 8, 3).direction == "bearish"
    assert market_call([], 0, 0, 0) is None


async def test_signals_job_saves_batch_and_resolve_scores_it(tmp_path):
    sessions = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    start = utcnow() - timedelta(hours=25)
    with sessions() as s:
        s.add_all([coin("a", "AAA", at=start), coin("b", "BBB", at=start),
                   coin("hot", "HOT", price=1.0, ch1h=6.0, ch24h=25.0, vol=8e8, at=start)])
        s.commit()
        # Pretend the batch was made 25h ago by running against those ticks, then backdating.
    async with make_client() as client:
        await Signals().run(client, sessions)
    with sessions() as s:
        for p in s.query(Prediction):
            p.created_at = start
        later = start + timedelta(hours=24, minutes=5)
        s.add_all([coin("a", "AAA", at=later), coin("b", "BBB", at=later),
                   coin("hot", "HOT", price=1.3, at=later)])
        s.commit()
        assert resolve(s) >= 2
        hot = s.query(Prediction).filter_by(kind="runner", symbol="HOT").one()
        assert hot.hit is True and round(hot.outcome_pct) == 30

        data = latest_signals(s)
        assert data["runners"][0]["symbol"] == "HOT"
        assert data["record"]["runner_hit_rate"] == 1.0


def test_should_send():
    crit = Alert(kind="hack", severity="critical", title="x", dedupe_key="1")
    info = Alert(kind="crash", severity="info", title="x", dedupe_key="2")
    wallet = Alert(kind="wallet", severity="info", title="x", dedupe_key="3")
    picks = Alert(kind="picks", severity="info", title="x", dedupe_key="4")
    assert should_send(crit, "critical", set())
    assert not should_send(info, "critical", set())
    assert should_send(wallet, "critical", {"wallet"})
    assert should_send(picks, "critical", set())


async def test_notifier_pushes_new_alerts_once_and_skips_backlog(tmp_path):
    sessions = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    with sessions() as s:
        s.add(Alert(dedupe_key="old", kind="hack", severity="critical", title="old hack",
                    created_at=utcnow() - timedelta(hours=2)))
        s.add(Alert(dedupe_key="new", kind="hack", severity="critical", title="Bridge drained", url="https://x/h"))
        s.add(Alert(dedupe_key="meh", kind="crash", severity="warn", title="meh"))
        s.commit()

    notifier = Notifier(ntfy_topic="crawler-test", ntfy_server="https://ntfy.sh", telegram_token="", telegram_chat="",
                        min_severity="critical", kinds=set())
    with respx.mock:
        route = respx.post("https://ntfy.sh/crawler-test").mock(return_value=httpx.Response(200))
        async with make_client() as client:
            assert await notifier.run(client, sessions) == (3, 1)
            assert await notifier.run(client, sessions) == (0, 0)
    assert route.call_count == 1
    req = route.calls[0].request
    assert req.content == b"Bridge drained" and req.headers["Click"] == "https://x/h"
