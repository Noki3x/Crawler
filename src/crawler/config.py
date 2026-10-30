import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///crawler.db")
    coingecko_api_key: str = os.getenv("COINGECKO_API_KEY", "")
    coingecko_top_n: int = int(os.getenv("COINGECKO_TOP_N", "250"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    user_agent: str = "CryptoNewsCrawler/0.1 (+https://github.com/Noki3x/Crawler)"


settings = Settings()
