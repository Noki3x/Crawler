import os
from dataclasses import dataclass

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
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    user_agent: str = "CryptoNewsCrawler/0.1 (+https://github.com/Noki3x/Crawler)"


settings = Settings()
