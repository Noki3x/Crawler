import argparse
import asyncio
import logging

from sqlalchemy import func, select

from .analysis import all_jobs
from .collectors import all_collectors
from .config import settings
from .db import Alert, CoinTick, CollectorRun, DexTick, Item, ItemAnalysis, make_session_factory
from .runner import Bot, run_all
from .wallets import WalletTracker


def status(sessions) -> None:
    with sessions() as session:
        print("Items by kind:")
        for kind, count in session.execute(select(Item.kind, func.count()).group_by(Item.kind)):
            print(f"  {kind:<12} {count}")
        print(f"Coin ticks:   {session.scalar(select(func.count()).select_from(CoinTick))}")
        print(f"DEX ticks:    {session.scalar(select(func.count()).select_from(DexTick))}")
        print(f"Analysed:     {session.scalar(select(func.count()).select_from(ItemAnalysis))}")
        print("\nLatest alerts:")
        for a in session.scalars(select(Alert).order_by(Alert.id.desc()).limit(10)):
            print(f"  [{a.severity:<8}] {a.kind:<13} {a.title[:90]}")
        print("\nLatest runs:")
        runs = session.scalars(select(CollectorRun).order_by(CollectorRun.id.desc()).limit(10))
        for r in runs:
            result = f"ERROR {r.error}" if r.error else f"{r.fetched} fetched, {r.stored} new"
            print(f"  {r.started_at:%Y-%m-%d %H:%M:%S}  {r.collector:<16} {result}")


def cli() -> None:
    parser = argparse.ArgumentParser(prog="crawler")
    parser.add_argument("command", choices=["serve", "run", "once", "status"], help=(
        "serve: run the bot with the web terminal; run: run the bot without it; "
        "once: one round of every job; status: show what's stored"
    ))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--only", nargs="*", help="limit to these collector names")
    args = parser.parse_args()

    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sessions = make_session_factory(settings.database_url)

    if args.command == "status":
        status(sessions)
        return
    jobs = [j for j in [*all_collectors(), *all_jobs(), WalletTracker()] if not args.only or j.name in args.only]
    if args.command == "serve":
        if not settings.dashboard_password:
            parser.error("set DASHBOARD_PASSWORD before starting the terminal")
        import uvicorn

        from .web.app import create_app

        uvicorn.run(create_app(Bot(jobs, sessions), sessions), host=args.host, port=args.port,
                    log_level=settings.log_level.lower())
        return
    asyncio.run(run_all(jobs, sessions, forever=args.command == "run"))


if __name__ == "__main__":
    cli()
