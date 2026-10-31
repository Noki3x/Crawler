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


TRON = "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf"
EVM = "0x28C6c06298d514Db089934071355E5743bf21d60"


def test_validate_tron_address():
    assert validate_address("tron", TRON) is None
    assert "doesn't look like" in validate_address("tron", "T0O" + TRON[3:])


async def test_tron_wallet_tracks_trx_and_usdt(tmp_path):
    sessions = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    with sessions() as s:
        s.add(Wallet(label="FOMO", chain="tron", address=TRON))
        s.commit()
    account = {"data": [{"balance": 2_000_000, "trc20": [{"TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": "150250000"}]}]}
    with respx.mock:
        respx.get(f"https://api.trongrid.io/v1/accounts/{TRON}").mock(return_value=httpx.Response(200, json=account))
        async with make_client() as client:
            assert await WalletTracker().run(client, sessions) == (1, 2)
    with sessions() as s:
        [w] = wallet_summary(s)
        assert [(h["symbol"], h["amount"]) for h in w["holdings"]] == [("USDT", 150.25), ("TRX", 2.0)]
        assert w["total_usd"] == 150.25


async def test_evm_wallet_tracks_stablecoins(tmp_path):
    sessions = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    with sessions() as s:
        s.add(Wallet(label="Base", chain="base", address=EVM))
        s.commit()

    def handler(request):
        body = __import__("json").loads(request.content)
        if body["method"] == "eth_getBalance":
            return httpx.Response(200, json={"result": hex(10**18)})
        usdc = body["params"][0]["to"].lower() == "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
        return httpx.Response(200, json={"result": hex(42_500_000 if usdc else 0)})

    with respx.mock:
        respx.post("https://base-rpc.publicnode.com").mock(side_effect=handler)
        async with make_client() as client:
            await WalletTracker().run(client, sessions)
    with sessions() as s:
        [w] = wallet_summary(s)
        assert {h["symbol"]: h["amount"] for h in w["holdings"]} == {"ETH": 1.0, "USDC": 42.5}
