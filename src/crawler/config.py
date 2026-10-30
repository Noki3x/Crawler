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
    # Coins need at least this much 24h volume to be picked as runners.
    runner_min_volume: float = float(os.getenv("RUNNER_MIN_VOLUME", "5000000"))
    dex_runner_min_liquidity: float = float(os.getenv("DEX_RUNNER_MIN_LIQUIDITY", "100000"))
    # The day's top-runner picks are sent on the first signals run at or after this hour (UTC).
    daily_picks_hour_utc: int = int(os.getenv("DAILY_PICKS_HOUR_UTC", "12"))
    # Phone alerts. ntfy is the quickest: install the ntfy app and subscribe to a long random topic name.
    ntfy_topic: str = os.getenv("NTFY_TOPIC", "")
    ntfy_server: str = os.getenv("NTFY_SERVER", "https://ntfy.sh")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    # info | warn | critical. Daily picks and market-call flips are always sent.
    notify_min_severity: str = os.getenv("NOTIFY_MIN_SEVERITY", "critical")
    # Alert kinds pushed whatever their severity (comma-separated). Wallet moves by default.
    notify_kinds: set = field(default_factory=lambda: {
        k.strip() for k in os.getenv("NOTIFY_KINDS", "wallet").split(",") if k.strip()})
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    user_agent: str = "CryptoNewsCrawler/0.1 (+https://github.com/Noki3x/Crawler)"


settings = Settings()
