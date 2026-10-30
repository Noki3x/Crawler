from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx

from ..records import Record


class Collector(ABC):
    name: str
    interval_seconds: int

    @abstractmethod
    async def collect(self, client: httpx.AsyncClient) -> list[Record]:
        """Fetch one round of data. Raise on failure; the runner logs it and retries next cycle."""


def from_unix(ts: float | int | None) -> datetime | None:
    if ts is None:
        return None
    if ts > 1e12:  # milliseconds
        ts = ts / 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
