import asyncio
import logging

import httpx
from sqlalchemy.orm import Session, sessionmaker

from .collectors import Collector
from .config import settings
from .db import CollectorRun, store, utcnow
from .jobs import Job

log = logging.getLogger(__name__)


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    )


async def run_once(collector: Collector | Job, client: httpx.AsyncClient, sessions: sessionmaker[Session]) -> CollectorRun:
    run = CollectorRun(collector=collector.name, started_at=utcnow())
    try:
        if isinstance(collector, Job):
            run.fetched, run.stored = await collector.run(client, sessions)
        else:
            records = await collector.collect(client)
            run.fetched = len(records)
            with sessions() as session:
                run.stored = store(session, records)
        log.info("%s: fetched %d, stored %d new", collector.name, run.fetched, run.stored)
    except Exception as exc:
        run.error = f"{type(exc).__name__}: {exc}"
        log.error("%s failed: %s", collector.name, run.error)
    run.finished_at = utcnow()
    with sessions() as session:
        session.add(run)
        session.commit()
    return run


async def run_forever(collector: Collector | Job, client: httpx.AsyncClient, sessions: sessionmaker[Session]) -> None:
    while True:
        await run_once(collector, client, sessions)
        await asyncio.sleep(collector.interval_seconds)


async def run_all(collectors: list[Collector | Job], sessions: sessionmaker[Session], forever: bool) -> list[CollectorRun]:
    async with make_client() as client:
        if forever:
            await asyncio.gather(*(run_forever(c, client, sessions) for c in collectors))
            return []
        return list(await asyncio.gather(*(run_once(c, client, sessions) for c in collectors)))
