---
name: scoring-restart
description: When the user has just modified files under app/scoring/, app/backtest/, app/risk.py, or app/portfolio.py — or after any change that would alter score outputs — run restart.bat so the signal_history snapshot is recomputed. Otherwise the radar/watchlist (which reads the snapshot) and the stock detail page (which calls score_stock live) will disagree.
---

# scoring-restart

## When this skill triggers

Trigger this skill **immediately after** an Edit/Write tool call has modified any of:

- `app/scoring/**/*.py` — score engine / rubric / radar
- `app/backtest/**/*.py` — backtest engine
- `app/risk.py` — risk gates / circuit breakers
- `app/portfolio.py` — tax / fee helpers used by both live and backtest
- `app/data/adjuster.py` — split/dividend adjustment
- Any change that affects fields read inside `score_all()` or `score_stock()`

## Why this matters

`signal_history` is a snapshot table written by `score_all()` during `market_update`. The radar page and watchlist read from this snapshot. The individual stock detail page calls `score_stock()` live.

`ensure_fresh()` only re-runs `score_all()` when `as_of < daily_price.MAX(date)` — so if you change scoring code but the date hasn't advanced, **the snapshot keeps the old numbers**. Result: detail page shows new logic, radar/watchlist show old logic, and they will silently disagree.

`restart.bat` does: stop servers → force `snapshot_today()` → relaunch. This is the fix.

## What to do

1. Run the test suite first to confirm the change is internally consistent:
   ```
   python -m pytest tests/ -q
   ```
2. If tests pass, run `restart.bat` from the project root:
   ```
   ./restart.bat
   ```
   (On Windows shell — `restart.bat` chains `_kill-servers.bat` → snapshot → `_launch-servers.bat`.)
3. Tell the user the snapshot was rebuilt so radar/watchlist match the new logic.

## What NOT to do

- Do not run `launch.bat` directly — it will not regenerate the snapshot.
- Do not skip the test step. If `score_all` and `score_stock` end up reading different `fund_snap` columns, both pages will compute but **disagree** — tests catch the most common form of this drift.
- Do not edit `signal_history` rows directly to "fix" stale numbers. Always rebuild via the snapshot.

## Cross-reference

- Live score path: `app/scoring/engine.py::score_stock` — calls `industry_yield_z_for_stock`.
- Snapshot score path: `app/scoring/radar.py::score_all` — injects `dividend_yield_z`.
- If you add a new pre-loaded field for the rubric, **both paths** must populate it. This is the most common source of drift.
