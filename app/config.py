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
        return cls(
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


def load_watchlist(path: Path | str = PROJECT_ROOT / "watchlist.yaml") -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return {str(k): str(v) for k, v in raw["stocks"].items()}
