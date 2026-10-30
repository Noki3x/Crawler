import httpx
import respx

from crawler.config import settings
from crawler.db import Alert, CoinTick, Wallet, WalletBalance, make_session_factory, utcnow
from crawler.runner import make_client
from crawler.wallets import WalletTracker, validate_address, wallet_summary

PHANTOM = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
MINT = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"


def test_validate_address():
    assert validate_address("solana", PHANTOM) is None
    assert validate_address("bitcoin", "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh") is None
    assert "seed phrase" in validate_address("solana", "word " * 12)
    assert "unsupported" in validate_address("dogechain", "x")


def solana_rpc(lamports, tokens):
    def handler(request):
        body = __import__("json").loads(request.content)
        if body["method"] == "getBalance":
            return httpx.Response(200, json={"result": {"value": lamports}})
        program = body["params"][1]["programId"]
        value = tokens if program.startswith("Tokenkeg") else []
        return httpx.Response(200, json={"result": {"value": [
            {"account": {"data": {"parsed": {"info": {"mint": m, "tokenAmount": {"uiAmountString": str(a)}}}}}}
            for m, a in value
        ]}})
    return handler


async def test_solana_wallet_balances_and_movements(tmp_path):
    sessions = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    with sessions() as s:
        s.add(Wallet(label="Phantom", chain="solana", address=PHANTOM))
        s.add(CoinTick(coin_id="solana", symbol="SOL", name="Solana", price_usd=200.0, market_cap_usd=1e11,
                       volume_24h_usd=1e9, change_1h_pct=0, change_24h_pct=0, change_7d_pct=0, taken_at=utcnow()))
        s.commit()

    dex = [{"baseToken": {"address": MINT, "symbol": "WIF"}, "priceUsd": "2.5", "liquidity": {"usd": 1e6}}]
    with respx.mock:
        respx.get(f"https://api.dexscreener.com/tokens/v1/solana/{MINT}").mock(return_value=httpx.Response(200, json=dex))
        rpc = respx.post(settings.solana_rpc_url)
        async with make_client() as client:
            rpc.side_effect = solana_rpc(2_000_000_000, [(MINT, 100)])
            assert await WalletTracker().run(client, sessions) == (1, 2)
            rpc.side_effect = solana_rpc(1_000_000_000, [(MINT, 300)])
            await WalletTracker().run(client, sessions)

    with sessions() as s:
        [w] = wallet_summary(s)
        assert w["total_usd"] == 1 * 200 + 300 * 2.5
        assert [h["symbol"] for h in w["holdings"]] == ["WIF", "SOL"]
        titles = sorted(a.title for a in s.query(Alert))
        assert titles == ["Phantom: received 200 WIF ($500)", "Phantom: sent 1 SOL ($200)"]
        assert s.query(WalletBalance).count() == 4
