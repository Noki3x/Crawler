import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings
from ..db import Wallet, WalletBalance
from ..runner import Bot
from ..wallets import validate_address, wallet_summary
from . import queries

STATIC = Path(__file__).parent / "static"
security = HTTPBasic(realm="Crawler terminal")


def require_password(creds: HTTPBasicCredentials = Depends(security)) -> None:
    if not secrets.compare_digest(creds.password.encode(), settings.dashboard_password.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": 'Basic realm="Crawler terminal"'})


class NewWallet(BaseModel):
    label: str
    chain: str
    address: str


def create_app(bot: Bot, sessions: sessionmaker[Session], start_bot: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(bot.run()) if start_bot else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Crawler terminal", lifespan=lifespan, dependencies=[Depends(require_password)])

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/overview")
    def overview():
        with sessions() as s:
            return queries.overview(s)

    @app.get("/api/feed")
    def feed(importance: str | None = None, kind: str | None = None, q: str | None = None,
             limit: int = 100, include_noise: bool = False):
        with sessions() as s:
            return queries.feed(s, importance, kind, q, limit=min(limit, 500), include_noise=include_noise)

    @app.get("/api/alerts")
    def alerts(limit: int = 100):
        with sessions() as s:
            return queries.alerts(s, limit=min(limit, 500))

    @app.get("/api/runs")
    def runs(limit: int = 100):
        with sessions() as s:
            return queries.runs(s, limit=min(limit, 500))

    @app.get("/api/jobs")
    def jobs():
        return [state.to_dict() for state in bot.states.values()]

    @app.post("/api/jobs/{name}/{action}")
    def control(name: str, action: str):
        if name not in bot.states:
            raise HTTPException(404, f"no job called {name!r}")
        actions = {"pause": bot.pause, "resume": bot.resume, "run": bot.run_now}
        if action not in actions:
            raise HTTPException(400, "action must be pause, resume or run")
        actions[action](name)
        return bot.states[name].to_dict()

    @app.get("/api/wallets")
    def wallets():
        with sessions() as s:
            return wallet_summary(s)

    @app.post("/api/wallets", status_code=201)
    def add_wallet(body: NewWallet):
        chain = body.chain.strip().lower()
        error = validate_address(chain, body.address)
        if error:
            raise HTTPException(400, error)
        with sessions() as s:
            if s.scalar(select(Wallet.id).where(Wallet.chain == chain, Wallet.address == body.address.strip())):
                raise HTTPException(409, "that wallet is already tracked")
            w = Wallet(label=body.label.strip()[:64] or chain, chain=chain, address=body.address.strip())
            s.add(w)
            s.commit()
            wallet_id = w.id
        if "wallets" in bot.states:
            bot.run_now("wallets")
        return {"id": wallet_id}

    @app.delete("/api/wallets/{wallet_id}", status_code=204)
    def delete_wallet(wallet_id: int):
        with sessions() as s:
            w = s.get(Wallet, wallet_id)
            if w is None:
                raise HTTPException(404)
            s.query(WalletBalance).filter_by(wallet_id=wallet_id).delete()
            s.delete(w)
            s.commit()

    @app.get("/api/stream")
    async def stream():
        """Server-sent events with anything new: items, alerts and bot runs."""
        async def events():
            with sessions() as s:
                last = {
                    "items": max((r["id"] for r in queries.feed(s, limit=1, include_noise=True)), default=0),
                    "alerts": max((r["id"] for r in queries.alerts(s, limit=1)), default=0),
                    "runs": max((r["id"] for r in queries.runs(s, limit=1)), default=0),
                }
            yield "retry: 3000\n\n"
            while True:
                await asyncio.sleep(3)
                with sessions() as s:
                    fresh = {
                        "items": queries.feed(s, after_id=last["items"], limit=200, include_noise=True),
                        "alerts": queries.alerts(s, after_id=last["alerts"]),
                        "runs": queries.runs(s, after_id=last["runs"]),
                    }
                    # Items get their AI tags a minute after they arrive; resend recently tagged ones.
                    fresh["tagged"] = queries.feed(s, limit=40, include_noise=True)
                for kind in ("items", "alerts", "runs"):
                    if fresh[kind]:
                        last[kind] = max(r["id"] for r in fresh[kind])
                fresh["jobs"] = [st.to_dict() for st in bot.states.values()]
                yield f"data: {json.dumps(fresh)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
