from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FinMindConfig:
    token: str
    base_url: str


@dataclass
class DatabaseConfig:
    path: Path


@dataclass
class FetchConfig:
    start_date: str
    request_delay: float


@dataclass
class LoggingConfig:
    level: str
    file: Path


@dataclass
class BrokerConfig:
    fee_discount: float = 1.0   # 手續費折扣（0.28 = 28 折）
    fee_min: float = 0          # 單筆最低手續費（元）；無低銷設 0


@dataclass
class Config:
    finmind: FinMindConfig
    database: DatabaseConfig
    fetch: FetchConfig
    logging: LoggingConfig
    broker: BrokerConfig
    notify: dict  # 推播設定，結構見 app/notifier.py
    backup: dict  # 備份設定，結構見 app/backup.py

    @classmethod
    def load(cls, path: Path | str = PROJECT_ROOT / "config.yaml") -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        finmind_raw = dict(raw["finmind"])
        # 環境變數優先：FINMIND_TOKEN 會覆寫 yaml 裡的 token
        env_token = os.environ.get("FINMIND_TOKEN")
        if env_token:
            finmind_raw["token"] = env_token

        broker_raw = raw.get("broker") or {}
        notify_raw = raw.get("notify") or {}
        backup_raw = raw.get("backup") or {}
        obj = cls(
            finmind=FinMindConfig(**finmind_raw),
            database=DatabaseConfig(path=PROJECT_ROOT / raw["database"]["path"]),
            fetch=FetchConfig(**raw["fetch"]),
            logging=LoggingConfig(
                level=raw["logging"]["level"],
                file=PROJECT_ROOT / raw["logging"]["file"],
            ),
            broker=BrokerConfig(**broker_raw),
            notify=notify_raw,
            backup=backup_raw,
        )
        obj._validate()
        return obj

    def _validate(self) -> None:
        """啟動即驗證關鍵設定，把組態錯誤提早到第一次 load（而非第一個 data fetch 才炸）。

        - finmind.token 為空 → 直接報錯（含修法提示）。環境變數 FINMIND_TOKEN 已在上面覆寫。
        - 資料庫目錄不存在 → 報錯（sqlite 連不上時的錯誤訊息很難懂，這裡先擋）。
        - log 目錄不存在 → 順手建好（非致命，免使用者手動 mkdir）。
        """
        if not (self.finmind.token or "").strip():
            raise ValueError(
                "config.yaml 的 finmind.token 為空：請填入 token，或設環境變數 FINMIND_TOKEN（優先）。"
            )
        db_parent = self.database.path.parent
        if not db_parent.exists():
            raise ValueError(
                f"資料庫目錄不存在：{db_parent}。請建立該資料夾，或修正 config.yaml 的 database.path。"
            )
        try:
            self.logging.file.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def load_watchlist(path: Path | str = PROJECT_ROOT / "watchlist.yaml") -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return {str(k): str(v) for k, v in raw["stocks"].items()}
