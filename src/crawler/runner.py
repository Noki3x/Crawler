import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

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


@dataclass
class JobState:
    job: Collector | Job
    paused: bool = False
    running: bool = False
    force: bool = False
    last_run: CollectorRun | None = None
    next_run_at: datetime | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def to_dict(self) -> dict:
        last = self.last_run
        return {
            "name": self.job.name,
            "interval_seconds": self.job.interval_seconds,
            "paused": self.paused,
            "running": self.running,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "last_run": None if last is None else {
                "started_at": last.started_at.isoformat(),
                "fetched": last.fetched,
                "stored": last.stored,
                "error": last.error,
            },
        }


class Bot:
    """Runs every job on its own schedule and lets the terminal pause, resume or trigger them."""

    def __init__(self, jobs: list[Collector | Job], sessions: sessionmaker[Session]):
        self.sessions = sessions
        self.states = {j.name: JobState(j) for j in jobs}

    def pause(self, name: str) -> None:
        self.states[name].paused = True

    def resume(self, name: str) -> None:
        state = self.states[name]
        state.paused = False
        state.wake.set()

    def run_now(self, name: str) -> None:
        state = self.states[name]
        state.force = True
        state.wake.set()

    async def _loop(self, state: JobState, client: httpx.AsyncClient) -> None:
        if isinstance(state.job, Job):
            await self._collected.wait()  # analysis jobs start once every collector has a first result
        while True:
            if not state.paused or state.force:
                state.force = False
                state.running = True
                try:
                    state.last_run = await run_once(state.job, client, self.sessions)
                except Exception:
                    # One bad run (e.g. the database briefly unavailable) must not stop the bot.
                    log.exception("%s: run failed", state.job.name)
                finally:
                    state.running = False
                    if not isinstance(state.job, Job):
                        self._pending.discard(state.job.name)
                        if not self._pending:
                            self._collected.set()
            state.next_run_at = None if state.paused else utcnow() + timedelta(seconds=state.job.interval_seconds)
            state.wake.clear()
            try:
                await asyncio.wait_for(state.wake.wait(), timeout=state.job.interval_seconds)
            except TimeoutError:
                pass

    async def run(self) -> None:
        self._pending = {n for n, s in self.states.items() if not isinstance(s.job, Job)}
        self._collected = asyncio.Event()
        if not self._pending:
            self._collected.set()
        async with make_client() as client:
            await asyncio.gather(*(self._loop(s, client) for s in self.states.values()))


async def run_all(collectors: list[Collector | Job], sessions: sessionmaker[Session], forever: bool) -> list[CollectorRun]:
    if forever:
        await Bot(collectors, sessions).run()
        return []
    # Collectors first, so the analysis jobs in the same round see fresh data.
    fetchers = [c for c in collectors if not isinstance(c, Job)]
    jobs = [c for c in collectors if isinstance(c, Job)]
    async with make_client() as client:
        runs = list(await asyncio.gather(*(run_once(c, client, sessions) for c in fetchers)))
        return runs + list(await asyncio.gather(*(run_once(j, client, sessions) for j in jobs)))
