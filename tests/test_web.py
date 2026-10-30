from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from crawler.config import settings
from crawler.db import Alert, CoinTick, Item, ItemAnalysis, make_session_factory, utcnow
from crawler.runner import Bot
from crawler.wallets import WalletTracker
from crawler.web.app import create_app

PASSWORD = "hunter2"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("crawler.web.app.settings", replace(settings, dashboard_password=PASSWORD))
    sessions = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    with sessions() as s:
        a = Item(url_hash="a", source="coindesk", kind="news", title="ETF approved", url="https://x/a")
        b = Item(url_hash="b", source="coindesk", kind="news", title="Top 5 memes", url="https://x/b")
        s.add_all([a, b])
        s.flush()
        s.add_all([
            ItemAnalysis(item_id=a.id, importance="major", sentiment="bullish", category="etf",
                         coins=["SOL"], summary="ETF approved.", confidence=0.9, model="t"),
            ItemAnalysis(item_id=b.id, importance="noise", sentiment="neutral", category="other",
                         coins=[], summary="", confidence=0.9, model="t"),
            Alert(dedupe_key="k", kind="hack", severity="critical", title="X hacked"),
            CoinTick(coin_id="bitcoin", symbol="BTC", name="Bitcoin", price_usd=100_000, market_cap_usd=2e12,
                     volume_24h_usd=5e10, change_1h_pct=0.1, change_24h_pct=2.0, change_7d_pct=5.0, taken_at=utcnow()),
        ])
        s.commit()
    bot = Bot([WalletTracker()], sessions)
    with TestClient(create_app(bot, sessions, start_bot=False)) as c:
        c.auth = ("me", PASSWORD)
        yield c


def test_requires_password(client):
    client.auth = ("me", "wrong")
    assert client.get("/api/feed").status_code == 401
    client.auth = None
    assert client.get("/").status_code == 401


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "CRAWLER" in r.text


def test_feed_hides_noise_by_default(client):
    titles = [i["title"] for i in client.get("/api/feed").json()]
    assert titles == ["ETF approved"]
    assert len(client.get("/api/feed", params={"include_noise": True}).json()) == 2
    [major] = client.get("/api/feed", params={"importance": "major"}).json()
    assert major["coins"] == ["SOL"] and major["sentiment"] == "bullish"


def test_overview_and_alerts(client):
    ov = client.get("/api/overview").json()
    assert ov["majors"][0]["symbol"] == "BTC"
    assert ov["market"]["news_bullish"] == 1
    assert client.get("/api/alerts").json()[0]["title"] == "X hacked"


def test_job_controls(client):
    assert client.post("/api/jobs/wallets/pause").json()["paused"] is True
    assert client.post("/api/jobs/wallets/resume").json()["paused"] is False
    assert client.post("/api/jobs/nope/pause").status_code == 404
    assert client.post("/api/jobs/wallets/explode").status_code == 400


def test_wallet_add_validates_and_rejects_secrets(client):
    seed = "abandon " * 11 + "about"
    r = client.post("/api/wallets", json={"label": "x", "chain": "solana", "address": seed})
    assert r.status_code == 400 and "seed phrase" in r.json()["detail"]
    r = client.post("/api/wallets", json={"label": "Ledger", "chain": "ethereum", "address": "0x123"})
    assert r.status_code == 400
    addr = "0x" + "ab" * 20
    assert client.post("/api/wallets", json={"label": "Ledger", "chain": "ethereum", "address": addr}).status_code == 201
    assert client.post("/api/wallets", json={"label": "dup", "chain": "ethereum", "address": addr}).status_code == 409
    [w] = client.get("/api/wallets").json()
    assert w["label"] == "Ledger"
    assert client.delete(f"/api/wallets/{w['id']}").status_code == 204
    assert client.get("/api/wallets").json() == []
