"""FastAPI app 入口。啟動：
    .venv/Scripts/python.exe -m uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.routers import alerts, backtest, calendar, dashboard, diagnostics, dq, history, market, portfolio, radar, search, stocks, system, watchlist, weight_tuner

# Logger 設定：basicConfig 對 root 已有 handler 時是 no-op，所以 uvicorn 自己接管時不會干擾。
# 純 python 直跑（測試 / scripts import）才有效，目的就是不要讓 api.* 的 INFO 訊息靜默掉。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

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

api_logger = logging.getLogger("api")
request_logger = logging.getLogger("api.request")


@app.middleware("http")
async def request_timing_log(request: Request, call_next):
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        request_logger.info(
            "%s %s -> %s (%.1f ms)",
            request.method,
            request.url.path,
            status_code,
            elapsed_ms,
        )


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    """攔下所有 HTTPException：把 detail 印到 log，方便用「使用者回報 422」反查後端訊息。

    維持原本 response shape（FastAPI 預設就是 {"detail": ...}），所以前端不用動。
    headers 會原樣 forward，covers WWW-Authenticate 等 401 場景。
    """
    if exc.status_code >= 500:
        api_logger.error("%s %s -> %d %s", request.method, request.url.path, exc.status_code, exc.detail)
    elif exc.status_code >= 400:
        api_logger.warning("%s %s -> %d %s", request.method, request.url.path, exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """未明確處理的 exception → 完整 traceback 入 log（含類別、檔案、行號）。

    回給前端只有 500 + exception class name；不洩漏內部訊息（避免 stacktrace
    經由錯誤頁外洩 SQL / path / 環境變數），但 log 端有完整資訊可 grep。
    """
    api_logger.exception(
        "Unhandled exception in %s %s: %s: %s",
        request.method, request.url.path, type(exc).__name__, exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "type": type(exc).__name__},
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
app.include_router(diagnostics.router)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}
