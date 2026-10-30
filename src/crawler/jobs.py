from abc import ABC, abstractmethod

import httpx
from sqlalchemy.orm import Session, sessionmaker


class Job(ABC):
    """Work that reads the database rather than the web: analysis, alerts, predictions."""

    name: str
    interval_seconds: int

    @abstractmethod
    async def run(self, client: httpx.AsyncClient, sessions: sessionmaker[Session]) -> tuple[int, int]:
        """Do one round. Returns (items looked at, rows written)."""
