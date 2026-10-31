"""Tracks your own wallets by public address: balances, value in USD, and what moved in or out."""
import logging
from datetime import datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .collectors.base import to_float
from .config import settings
from .db import CoinTick, Wallet, WalletBalance, add_alert, iso, utcnow
from .jobs import Job

log = logging.getLogger(__name__)

SPL_PROGRAMS = [
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
]

# chain -> (default public RPC, native symbol, CoinGecko id of the native coin)
EVM_CHAINS = {
    "ethereum": ("https://ethereum-rpc.publicnode.com", "ETH", "ethereum"),
    "base": ("https://base-rpc.publicnode.com", "ETH", "ethereum"),
    "arbitrum": ("https://arbitrum-one-rpc.publicnode.com", "ETH", "ethereum"),
    "optimism": ("https://optimism-rpc.publicnode.com", "ETH", "ethereum"),
    "bsc": ("https://bsc-rpc.publicnode.com", "BNB", "binancecoin"),
    "polygon": ("https://polygon-bor-rpc.publicnode.com", "POL", "polygon-ecosystem-token"),
}
# Stablecoins tracked on each EVM chain: symbol -> (contract, decimals). Counted at $1.
EVM_STABLES = {
    "ethereum": {"USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
                 "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6)},
    "base": {"USDT": ("0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2", 6),
             "USDC": ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6)},
    "arbitrum": {"USDT": ("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6),
                 "USDC": ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6)},
    "optimism": {"USDT": ("0x94b008aA00579c1307B0EF2c499aD98a8ce58e58", 6),
                 "USDC": ("0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", 6)},
    "bsc": {"USDT": ("0x55d398326f99059fF775485246999027B3197955", 18),
            "USDC": ("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18)},
    "polygon": {"USDT": ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
                "USDC": ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6)},
}
SOLANA_STABLES = {
    "Es9vMFrzaCERmJfrF4H2FYD4KCoFvUmE5GSRhEXTAU8": "USDT",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
}
TRON_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
CHAINS = ["solana", "bitcoin", "tron", *EVM_CHAINS]
BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def validate_address(chain: str, address: str) -> str | None:
    """Returns an error message, or None if the address looks right for the chain."""
    address = address.strip()
    if chain not in CHAINS:
        return f"unsupported chain {chain!r}; use one of {', '.join(CHAINS)}"
    if chain in EVM_CHAINS:
        ok = address.startswith("0x") and len(address) == 42
    elif chain == "solana":
        ok = 32 <= len(address) <= 44 and address.isalnum()
    elif chain == "tron":
        ok = address.startswith("T") and len(address) == 34 and set(address) <= BASE58
    else:
        ok = address[:1] in ("1", "3") or address.startswith("bc1")
    # Seed phrases have spaces; private keys are much longer than addresses.
    if " " in address or len(address) > 90:
        return "that looks like a seed phrase or private key. Only paste your public address, and never share the phrase or key"
    return None if ok else f"that doesn't look like a {chain} address"


async def rpc(client: httpx.AsyncClient, url: str, method: str, params: list):
    resp = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"{method}: {body['error']}")
    return body["result"]


def latest_prices(session: Session) -> dict[str, float]:
    latest = select(CoinTick.coin_id, func.max(CoinTick.taken_at).label("t")).group_by(CoinTick.coin_id).subquery()
    rows = session.execute(
        select(CoinTick.coin_id, CoinTick.price_usd)
        .join(latest, (CoinTick.coin_id == latest.c.coin_id) & (CoinTick.taken_at == latest.c.t))
    )
    return {coin_id: price for coin_id, price in rows if price is not None}


async def solana_holdings(client: httpx.AsyncClient, address: str) -> list[tuple[str, str, float]]:
    """Returns (symbol, mint, amount). Symbols for SPL tokens are filled in when pricing."""
    url = settings.solana_rpc_url
    lamports = (await rpc(client, url, "getBalance", [address]))["value"]
    holdings = [("SOL", "", lamports / 1e9)]
    for program in SPL_PROGRAMS:
        result = await rpc(client, url, "getTokenAccountsByOwner",
                           [address, {"programId": program}, {"encoding": "jsonParsed"}])
        for acct in result["value"]:
            info = acct["account"]["data"]["parsed"]["info"]
            amount = to_float(info["tokenAmount"].get("uiAmountString")) or 0.0
            if amount > 0:
                holdings.append(("", info["mint"], amount))
    return holdings


async def price_spl(client: httpx.AsyncClient, mints: list[str]) -> dict[str, tuple[str, float | None]]:
    """mint -> (symbol, USD price) from the most liquid DexScreener pair."""
    out: dict[str, tuple[str, float | None, float]] = {}
    for i in range(0, len(mints), 30):
        resp = await client.get(f"https://api.dexscreener.com/tokens/v1/solana/{','.join(mints[i:i + 30])}")
        resp.raise_for_status()
        for pair in resp.json():
            base = pair.get("baseToken") or {}
            mint = base.get("address")
            liq = to_float((pair.get("liquidity") or {}).get("usd")) or 0
            if mint in mints and (mint not in out or liq > out[mint][2]):
                out[mint] = (base.get("symbol", "?"), to_float(pair.get("priceUsd")), liq)
    return {m: (sym, price) for m, (sym, price, _) in out.items()}


async def evm_stables(client: httpx.AsyncClient, url: str, chain: str, address: str) -> list[WalletBalance]:
    rows = []
    for symbol, (contract, decimals) in EVM_STABLES.get(chain, {}).items():
        # balanceOf(address)
        data = "0x70a08231" + address[2:].lower().rjust(64, "0")
        raw = await rpc(client, url, "eth_call", [{"to": contract, "data": data}, "latest"])
        amount = int(raw, 16) / 10 ** decimals if raw and raw != "0x" else 0.0
        if amount > 0:
            rows.append(WalletBalance(symbol=symbol, token_address=contract, amount=amount, value_usd=amount))
    return rows


async def tron_balances(client: httpx.AsyncClient, address: str, prices: dict[str, float]) -> list[WalletBalance]:
    resp = await client.get(f"https://api.trongrid.io/v1/accounts/{address}")
    resp.raise_for_status()
    data = (resp.json().get("data") or [{}])[0]  # empty until the address first receives something
    trx = (data.get("balance") or 0) / 1e6
    price = prices.get("tron")
    rows = [WalletBalance(symbol="TRX", amount=trx, value_usd=trx * price if price is not None else None)]
    usdt = sum(int(t[TRON_USDT]) for t in data.get("trc20") or [] if TRON_USDT in t) / 1e6
    if usdt > 0:
        rows.append(WalletBalance(symbol="USDT", token_address=TRON_USDT, amount=usdt, value_usd=usdt))
    return rows


async def wallet_balances(client: httpx.AsyncClient, wallet: Wallet, prices: dict[str, float]) -> list[WalletBalance]:
    rows: list[WalletBalance] = []
    if wallet.chain == "solana":
        holdings = await solana_holdings(client, wallet.address)
        mints = [mint for _, mint, _ in holdings if mint and mint not in SOLANA_STABLES]
        spl = await price_spl(client, mints) if mints else {}
        for symbol, mint, amount in holdings:
            if mint in SOLANA_STABLES:
                symbol, price = SOLANA_STABLES[mint], 1.0
            elif mint:
                symbol, price = spl.get(mint, (mint[:4] + "…", None))
            else:
                price = prices.get("solana")
            rows.append(WalletBalance(symbol=symbol, token_address=mint, amount=amount,
                                      value_usd=amount * price if price is not None else None))
    elif wallet.chain == "bitcoin":
        resp = await client.get(f"https://mempool.space/api/address/{wallet.address}")
        resp.raise_for_status()
        data = resp.json()
        sats = sum(s["funded_txo_sum"] - s["spent_txo_sum"] for s in (data["chain_stats"], data["mempool_stats"]))
        price = prices.get("bitcoin")
        rows.append(WalletBalance(symbol="BTC", amount=sats / 1e8,
                                  value_usd=sats / 1e8 * price if price is not None else None))
    elif wallet.chain == "tron":
        rows = await tron_balances(client, wallet.address, prices)
    else:
        url, symbol, coin_id = EVM_CHAINS[wallet.chain]
        url = settings.evm_rpc_urls.get(wallet.chain, url)
        wei = int(await rpc(client, url, "eth_getBalance", [wallet.address, "latest"]), 16)
        price = prices.get(coin_id)
        rows.append(WalletBalance(symbol=symbol, amount=wei / 1e18,
                                  value_usd=wei / 1e18 * price if price is not None else None))
        rows += await evm_stables(client, url, wallet.chain, wallet.address)
    for r in rows:
        r.wallet_id = wallet.id
    return rows


def previous_snapshot(session: Session, wallet_id: int) -> dict[tuple[str, str], WalletBalance]:
    last = session.scalar(select(func.max(WalletBalance.taken_at)).where(WalletBalance.wallet_id == wallet_id))
    if last is None:
        return {}
    rows = session.scalars(select(WalletBalance).where(
        WalletBalance.wallet_id == wallet_id, WalletBalance.taken_at == last))
    return {(r.symbol, r.token_address): r for r in rows}


def movement_alerts(session: Session, wallet: Wallet, before: dict, after: list[WalletBalance], when: datetime) -> int:
    """An alert for each asset that came in or went out since the last snapshot."""
    if not before:
        return 0  # first snapshot: nothing to compare against
    now = {(r.symbol, r.token_address): r for r in after}
    created = 0
    for key in set(before) | set(now):
        old = before[key].amount if key in before else 0.0
        new = now[key].amount if key in now else 0.0
        delta = new - old
        if abs(delta) <= max(abs(old), abs(new)) * 1e-6:
            continue
        unit = (now.get(key) or before[key])
        price = (unit.value_usd / unit.amount) if unit.value_usd and unit.amount else None
        usd = abs(delta) * price if price else None
        if usd is not None and usd < settings.wallet_min_move_usd:
            continue
        verb = "received" if delta > 0 else "sent"
        worth = f" (${usd:,.0f})" if usd else ""
        created += add_alert(
            session, f"wallet:{wallet.id}:{key[0]}:{key[1]}:{when.isoformat()}",
            kind="wallet", severity="info",
            title=f"{wallet.label}: {verb} {abs(delta):,.4g} {key[0]}{worth}",
            detail=f"{wallet.chain} {wallet.address}", symbol=key[0], chain=wallet.chain,
            token_address=key[1],
        )
    return created


class WalletTracker(Job):
    name = "wallets"
    interval_seconds = 60

    async def run(self, client: httpx.AsyncClient, sessions: sessionmaker[Session]) -> tuple[int, int]:
        written = 0
        with sessions() as session:
            wallets = session.scalars(select(Wallet)).all()
            prices = latest_prices(session)
            for wallet in wallets:
                try:
                    rows = await wallet_balances(client, wallet, prices)
                except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
                    log.warning("wallet %s (%s) failed: %s", wallet.label, wallet.chain, exc)
                    continue
                when = utcnow()
                before = previous_snapshot(session, wallet.id)
                for r in rows:
                    r.taken_at = when
                session.add_all(rows)
                written += len(rows)
                movement_alerts(session, wallet, before, rows, when)
                session.commit()
        return len(wallets), written


def wallet_summary(session: Session) -> list[dict]:
    """Latest holdings per wallet with totals, for the terminal."""
    out = []
    for w in session.scalars(select(Wallet).order_by(Wallet.id)):
        snap = previous_snapshot(session, w.id)
        holdings = sorted(snap.values(), key=lambda r: r.value_usd or 0, reverse=True)
        out.append({
            "id": w.id, "label": w.label, "chain": w.chain, "address": w.address,
            "updated_at": iso(holdings[0].taken_at) if holdings else None,
            "total_usd": sum(r.value_usd or 0 for r in holdings),
            "holdings": [
                {"symbol": r.symbol, "token_address": r.token_address, "amount": r.amount, "value_usd": r.value_usd}
                for r in holdings
            ],
        })
    return out
