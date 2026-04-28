"""把 FastAPI 的 OpenAPI schema 導出到 docs/api-spec.json。

設計動機：
docs/api-spec.md 是手寫的 791 行表格，每次 router 改了 schema / 加 endpoint 都要記得同步，
不同步時會誤導前端開發者。從 FastAPI 直接吐 OpenAPI 是 single source of truth。

用法：
    python -m scripts.dump_openapi              # 寫入 docs/api-spec.json
    python -m scripts.dump_openapi --check      # 比對既有 spec.json 是否與 code 同步；不同 → exit 1（CI 檢查）

整合：
- daily-update.bat 結尾可以呼叫一次，讓 spec.json 永遠 reflect prod
- pre-commit hook 可以跑 --check，避免改 router 沒同步 spec
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_PATH = ROOT / "docs" / "api-spec.json"


def dump_spec() -> dict:
    """從 FastAPI app 取得 OpenAPI schema 字典。"""
    from api.main import app  # 延後 import：spec 不在 path 時才執行
    return app.openapi()


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump FastAPI OpenAPI schema to JSON.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只比對 code 與 docs/api-spec.json 是否同步；不寫入。不同 → exit 1。",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUT_PATH,
        help=f"輸出檔（預設 {OUT_PATH.relative_to(ROOT)}）",
    )
    args = parser.parse_args()

    spec = dump_spec()
    rendered = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True)

    if args.check:
        if not args.out.exists():
            print(f"[FAIL] {args.out} 不存在；先跑一次 `python -m scripts.dump_openapi`", file=sys.stderr)
            return 1
        existing = args.out.read_text(encoding="utf-8")
        if existing.strip() != rendered.strip():
            print(
                f"[FAIL] {args.out} 與 code 不同步。修法：跑 `python -m scripts.dump_openapi`，"
                "把產出的 spec 一併 commit。",
                file=sys.stderr,
            )
            return 1
        print(f"[OK] {args.out.relative_to(ROOT)} 與 code 同步。")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    print(f"[OK] OpenAPI schema 已寫入 {args.out.relative_to(ROOT)}（{len(spec.get('paths', {}))} 個 endpoint）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
