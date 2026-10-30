import argparse
import asyncio
import logging

from sqlalchemy import func, select

from .collectors import all_collectors
from .config import settings
from .db import CoinTick, CollectorRun, DexTick, Item, make_session_factory
from .runner import run_all


def status(sessions) -> None:
    with sessions() as session:
        print("Items by kind:")
        for kind, count in session.execute(select(Item.kind, func.count()).group_by(Item.kind)):
            print(f"  {kind:<12} {count}")
        print(f"Coin ticks:   {session.scalar(select(func.count()).select_from(CoinTick))}")
        print(f"DEX ticks:    {session.scalar(select(func.count()).select_from(DexTick))}")
        print("\nLatest runs:")
        runs = session.scalars(select(CollectorRun).order_by(CollectorRun.id.desc()).limit(10))
        for r in runs:
            result = f"ERROR {r.error}" if r.error else f"{r.fetched} fetched, {r.stored} new"
            print(f"  {r.started_at:%Y-%m-%d %H:%M:%S}  {r.collector:<16} {result}")


def cli() -> None:
    parser = argparse.ArgumentParser(prog="crawler")
    parser.add_argument("command", choices=["run", "once", "status"], help=(
        "run: collect forever on a schedule; once: one round of every collector; status: show what's stored"
    ))
    parser.add_argument("--only", nargs="*", help="limit to these collector names")
    args = parser.parse_args()

    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sessions = make_session_factory(settings.database_url)

    if args.command == "status":
        status(sessions)
        return
    collectors = [c for c in all_collectors() if not args.only or c.name in args.only]
    asyncio.run(run_all(collectors, sessions, forever=args.command == "run"))


if __name__ == "__main__":
    cli()
