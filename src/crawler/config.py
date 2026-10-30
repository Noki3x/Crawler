import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///crawler.db")
    coingecko_api_key: str = os.getenv("COINGECKO_API_KEY", "")
    coingecko_top_n: int = int(os.getenv("COINGECKO_TOP_N", "250"))
    reddit_client_id: str = os.getenv("REDDIT_CLIENT_ID", "")
    reddit_client_secret: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    # Claude does the analysis. The SDK reads ANTHROPIC_API_KEY itself.
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    anthropic_effort: str = os.getenv("ANTHROPIC_EFFORT", "low")
    # Below this much DEX liquidity a token gets a low-liquidity warning.
    low_liquidity_usd: float = float(os.getenv("LOW_LIQUIDITY_USD", "25000"))
    # Wallet tracking uses free public RPCs; swap in your own (Helius, Alchemy...) if they rate-limit you.
    solana_rpc_url: str = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    evm_rpc_urls: dict = field(default_factory=lambda: {
        chain: url for chain in ("ethereum", "base", "arbitrum", "optimism", "bsc", "polygon")
        if (url := os.getenv(f"{chain.upper()}_RPC_URL"))
    })
    # Wallet moves smaller than this (in USD) don't raise an alert.
    wallet_min_move_usd: float = float(os.getenv("WALLET_MIN_MOVE_USD", "5"))
    # Required to open the terminal. Anyone with it can see your wallets and control the bot.
    dashboard_password: str = os.getenv("DASHBOARD_PASSWORD", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    user_agent: str = "CryptoNewsCrawler/0.1 (+https://github.com/Noki3x/Crawler)"


settings = Settings()
