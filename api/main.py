"""FastAPI app 入口。啟動：
    .venv/Scripts/python.exe -m uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import alerts, backtest, calendar, dashboard, dq, history, market, portfolio, radar, search, stocks, system, watchlist, weight_tuner

app = FastAPI(
    title="台股研究儀表板 API",
    description="Next.js 前端的 FastAPI 後端，包裝既有 app.* 模組。",
    version="0.1.0",
)

# 開發期允許 Next.js dev server；正式環境走同 origin 反代 (見 docs)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router)
app.include_router(portfolio.router)
app.include_router(watchlist.router)
app.include_router(stocks.router)
app.include_router(dashboard.router)
app.include_router(radar.router)
app.include_router(history.router)
app.include_router(calendar.router)
app.include_router(backtest.router)
app.include_router(weight_tuner.router)
app.include_router(search.router)
app.include_router(dq.router)
app.include_router(system.router)
app.include_router(alerts.router)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}
