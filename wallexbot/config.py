"""Configuration: one JSON file (versioned in git) + secrets from the hackathon `.env`."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent          # wallex-bot/
ENV_FILE = ROOT.parent / ".env"                         # agentic-hackathon/.env (gitignored)
RUNS = ROOT / "runs"


@dataclass
class Secrets:
    wallex_key: str = ""
    wallex_subaccount: str = ""
    wallex_key_account: str = "main"    # "main" (Daii's personal account) or "hackathon"
    openai_key: str = ""
    boxapi_key: str = ""


def load_secrets() -> Secrets:
    env = {**dotenv_values(ENV_FILE), **{k: v for k, v in os.environ.items() if k.startswith(("WALLEX_", "OPENAI_", "BOXAPI_"))}}
    return Secrets(
        wallex_key=(env.get("WALLEX_API_KEY") or "").strip(),
        wallex_subaccount=(env.get("WALLEX_SUBACCOUNT_ID") or "").strip(),
        wallex_key_account=(env.get("WALLEX_KEY_ACCOUNT") or "main").strip().lower(),
        openai_key=(env.get("OPENAI_API_KEY") or "").strip(),
        boxapi_key=(env.get("BOXAPI_KEY") or "").strip(),
    )


@dataclass
class Config:
    raw: dict = field(default_factory=dict)

    def __getitem__(self, k):
        return self.raw[k]

    def get(self, k, default=None):
        return self.raw.get(k, default)

    @property
    def markets(self) -> list[str]:
        return self.raw["markets"]

    @property
    def window_end(self) -> datetime:
        return datetime.fromisoformat(self.raw["window"]["end_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else ROOT / "config.json"
    return Config(json.loads(p.read_text(encoding="utf-8")))
