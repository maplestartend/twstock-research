"""In-process smoke test for all FastAPI endpoints.

Usage:
    .venv/Scripts/python.exe scripts/smoke_test_api.py
"""
from __future__ import annotations

import io
import json
import sys
import traceback
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

# Capture stderr during import in case any module emits warnings/errors
_import_err = io.StringIO()
with redirect_stderr(_import_err):
    from api.main import app  # noqa: E402

client = TestClient(app)

results: list[dict[str, Any]] = []


def _summarize_payload(payload: Any) -> str:
    if payload is None:
        return "None"
    if isinstance(payload, list):
        return f"list[{len(payload)}]"
    if isinstance(payload, dict):
        keys = list(payload.keys())
        # Look for common list fields and show their counts
        list_counts = {k: len(v) for k, v in payload.items() if isinstance(v, list)}
        if list_counts:
            return f"dict[{len(keys)} keys, list_lens={list_counts}]"
        return f"dict[{len(keys)} keys: {keys[:8]}{'...' if len(keys)>8 else ''}]"
    if isinstance(payload, (int, float, str, bool)):
        return f"{type(payload).__name__}={payload!r}"
    return type(payload).__name__


def _check_fields(payload: Any) -> list[str]:
    """Heuristic sanity checks; returns list of suspicious notes."""
    notes: list[str] = []

    def walk_score(obj: Any, path: str = ""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}" if path else k
                if k == "score" and isinstance(v, (int, float)):
                    if v < 0 or v > 100:
                        notes.append(f"{p}={v} out of [0,100]")
                if k in ("date", "as_of", "updated_at") and isinstance(v, str):
                    if len(v) >= 10 and v[4] != "-":
                        notes.append(f"{p}={v} not YYYY-MM-DD-ish")
                walk_score(v, p)
        elif isinstance(obj, list) and obj:
            for i, item in enumerate(obj[:3]):
                walk_score(item, f"{path}[{i}]")

    walk_score(payload)
    return notes


def call(method: str, path: str, *, label: str | None = None, json_body: Any = None,
         expect_status: int | tuple[int, ...] = 200) -> None:
    full_label = label or f"{method} {path}"
    err_buf = io.StringIO()
    try:
        with redirect_stderr(err_buf):
            if method == "GET":
                resp = client.get(path)
            elif method == "POST":
                resp = client.post(path, json=json_body)
            elif method == "DELETE":
                resp = client.delete(path)
            else:
                raise ValueError(f"unknown method: {method}")
    except Exception as e:  # noqa: BLE001
        results.append({
            "label": full_label,
            "method": method,
            "path": path,
            "status": "EXC",
            "summary": "",
            "error": f"Exception: {type(e).__name__}: {e}",
            "traceback": traceback.format_exc() + "\nstderr:\n" + err_buf.getvalue(),
            "notes": [],
        })
        return

    status = resp.status_code
    notes: list[str] = []
    summary = ""
    err_msg = ""
    tb = ""
    payload: Any = None
    try:
        payload = resp.json()
    except Exception:
        payload = resp.text

    if status >= 400:
        err_msg = json.dumps(payload, ensure_ascii=False, default=str)[:2000]
        tb = err_buf.getvalue()
    else:
        summary = _summarize_payload(payload)
        notes = _check_fields(payload)

    # Compose expected check
    if isinstance(expect_status, int):
        ok = status == expect_status
    else:
        ok = status in expect_status

    results.append({
        "label": full_label,
        "method": method,
        "path": path,
        "status": status,
        "expected": expect_status,
        "ok_expected": ok,
        "summary": summary,
        "error": err_msg,
        "traceback": tb,
        "notes": notes,
        "payload_preview": json.dumps(payload, ensure_ascii=False, default=str)[:400] if payload is not None else "",
    })


# ----------------------------------------------------------------------------
# 1) Discover latest available date for /api/history/performance?as_of=
# ----------------------------------------------------------------------------
latest_date: str | None = None
try:
    r = client.get("/api/history/dates")
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, list) and data:
            # Could be list of strings or list of dicts
            first = data[-1] if isinstance(data[0], str) else data[0]
            if isinstance(first, str):
                latest_date = first
            elif isinstance(first, dict):
                for k in ("date", "as_of", "trade_date"):
                    if k in first:
                        latest_date = first[k]
                        break
        elif isinstance(data, dict):
            for k in ("dates", "items", "list"):
                if k in data and isinstance(data[k], list) and data[k]:
                    v0 = data[k][-1] if isinstance(data[k][0], str) else data[k][0]
                    if isinstance(v0, str):
                        latest_date = v0
                    elif isinstance(v0, dict):
                        for kk in ("date", "as_of", "trade_date"):
                            if kk in v0:
                                latest_date = v0[kk]
                                break
                    break
except Exception:
    pass

# ----------------------------------------------------------------------------
# GET endpoints
# ----------------------------------------------------------------------------
gets = [
    "/api/market/snapshot",
    "/api/market/breadth",
    "/api/market/industry-rotation",
    "/api/market/industry-members?industry=半導體業",
    "/api/portfolio/holdings",
    "/api/portfolio/summary",
    "/api/portfolio/risk-alerts",
    "/api/portfolio/trades",
    "/api/portfolio/realized-pnl",
    "/api/watchlist",
    "/api/watchlist/lookup/2330",
    "/api/watchlist/movers?direction=up",
    "/api/watchlist/overview",
    "/api/dashboard/radar-hits",
    "/api/dashboard/ex-dividend",
    "/api/dashboard/data-freshness",
    "/api/radar/strategies",
    "/api/radar/hits?top=20",
    "/api/radar/hits?strategy=短線強勢",
    "/api/history/dates",
    "/api/calendar/ex-dividend",
    "/api/weight-tuner/breakdown",
    "/api/health",
]
for p in gets:
    call("GET", p)

# Stocks endpoints across multiple stock_ids
for sid in ("2330", "1101", "9999", "0050"):
    call("GET", f"/api/stocks/{sid}/meta")
    call("GET", f"/api/stocks/{sid}/price?days=180")
    call("GET", f"/api/stocks/{sid}/score")
    call("GET", f"/api/stocks/{sid}/score-history?days=90")

# History performance with latest date discovered
if latest_date:
    call("GET", f"/api/history/performance?as_of={latest_date}", label=f"GET /api/history/performance?as_of={latest_date}")
else:
    results.append({
        "label": "GET /api/history/performance?as_of=<latest>",
        "method": "GET",
        "path": "/api/history/performance",
        "status": "SKIP",
        "summary": "could not discover latest date",
        "error": "",
        "traceback": "",
        "notes": ["latest_date=None — /api/history/dates returned non-list or empty"],
    })

# ----------------------------------------------------------------------------
# POST endpoints
# ----------------------------------------------------------------------------
default_cfg = {
    "entry_threshold": 65,
    "exit_threshold": 40,
    "stop_loss_pct": 0.08,
    "take_profit_pct": 0.20,
    "max_hold_days": 60,
    "slippage_bps": 5,
    "lookback_days": 500,
    "use_adj": True,
}

for sid in ("2330", "1101", "0050"):
    call("POST", "/api/backtest/stock",
         label=f"POST /api/backtest/stock [{sid}]",
         json_body={"stock_id": sid, "config": default_cfg})

call("POST", "/api/backtest/portfolio",
     label="POST /api/backtest/portfolio [2330,2317,2454]",
     json_body={"stock_ids": ["2330", "2317", "2454"], "config": default_cfg})
call("POST", "/api/backtest/portfolio",
     label="POST /api/backtest/portfolio [1101,1102]",
     json_body={"stock_ids": ["1101", "1102"], "config": default_cfg})

call("POST", "/api/backtest/grid-search",
     label="POST /api/backtest/grid-search",
     json_body={
         "stock_ids": ["2330", "2317"],
         "entry_list": [60, 65, 70],
         "exit_list": [35, 40],
         "sl_list": [0.08, 0.10],
         "tp_list": [0.15, 0.20],
         "max_hold_days": 60,
         "lookback_days": 500,
     })

call("POST", "/api/backtest/walk-forward",
     label="POST /api/backtest/walk-forward",
     json_body={
         "stock_ids": ["2330", "2317"],
         "entry_list": [60, 65],
         "exit_list": [35, 40],
         "sl_list": [0.08],
         "tp_list": [0.15],
         "max_hold_days": 60,
         "n_splits": 3,
         "train_ratio": 0.7,
     })

# Watchlist POST 9999 — should NOT actually add (expect 4xx). If it returns 2xx we DELETE to clean up.
call("POST", "/api/watchlist", label="POST /api/watchlist {stock_id:9999}",
     json_body={"stock_id": "9999"}, expect_status=(409, 400, 404, 422))
# Cleanup: try delete in case it was added
try:
    cleanup_resp = client.delete("/api/watchlist/9999")
    results.append({
        "label": "DELETE /api/watchlist/9999 (cleanup)",
        "method": "DELETE",
        "path": "/api/watchlist/9999",
        "status": cleanup_resp.status_code,
        "summary": "cleanup",
        "error": "",
        "traceback": "",
        "notes": [],
    })
except Exception as e:  # noqa: BLE001
    results.append({
        "label": "DELETE /api/watchlist/9999 (cleanup)",
        "method": "DELETE",
        "path": "/api/watchlist/9999",
        "status": "EXC",
        "summary": "",
        "error": str(e),
        "traceback": "",
        "notes": [],
    })


# ----------------------------------------------------------------------------
# Dump JSON
# ----------------------------------------------------------------------------
out_path = ROOT / "reports" / "smoke_test_api_results.json"
out_path.parent.mkdir(exist_ok=True)
out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nWrote {len(results)} results to {out_path}\n")

# ----------------------------------------------------------------------------
# Print human-readable summary
# ----------------------------------------------------------------------------
critical = [r for r in results if (isinstance(r["status"], int) and r["status"] >= 500) or r["status"] == "EXC"]
client_err = [r for r in results if isinstance(r["status"], int) and 400 <= r["status"] < 500]
suspicious = [r for r in results if isinstance(r["status"], int) and r["status"] < 400 and r.get("notes")]
ok = [r for r in results if isinstance(r["status"], int) and r["status"] < 400]

print("=" * 80)
print(f"TOTAL: {len(results)}  CRITICAL(5xx/EXC): {len(critical)}  4xx: {len(client_err)}  OK: {len(ok)}  SUSPICIOUS-OK: {len(suspicious)}")
print("=" * 80)

print("\n## CRITICAL (5xx / Exception)\n")
for r in critical:
    print(f"[{r['status']}] {r['label']}")
    if r.get("error"):
        print(f"  error: {r['error'][:500]}")
    if r.get("traceback"):
        print(f"  traceback (truncated):")
        for line in r["traceback"].splitlines()[-30:]:
            print(f"    {line}")
    print()

print("\n## CLIENT ERRORS (4xx)\n")
for r in client_err:
    expected = r.get("expected")
    flag = "OK-EXPECTED" if r.get("ok_expected") else "UNEXPECTED"
    print(f"[{r['status']}] {flag} expect={expected}  {r['label']}")
    if r.get("error"):
        print(f"  body: {r['error'][:300]}")

print("\n## SUSPICIOUS (200 with anomalies)\n")
for r in suspicious:
    print(f"[{r['status']}] {r['label']}  -> {r['summary']}")
    for n in r["notes"]:
        print(f"   note: {n}")
    print(f"   preview: {r.get('payload_preview','')[:200]}")

print("\n## OK\n")
for r in ok:
    if r in suspicious:
        continue
    print(f"[{r['status']}] {r['label']}  -> {r['summary']}")
