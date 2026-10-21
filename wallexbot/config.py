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


def market_tick_files(runs: Path = RUNS) -> list[Path]:
    """ticks.csv of real market runs only (live_*, shadow_*): test and smoke runs write fake ticks."""
    return [p for p in Path(runs).glob("*/ticks.csv") if p.parent.name.startswith(("live_", "shadow_"))]


def load_backtest(name: str, runs: Path = RUNS) -> dict | None:
    """runs/backtests/<name>/summary.json (scripts/bt_fairvalue.py) as {"note", "markets"}, or None when the
    file is missing or malformed: it is optional evidence for the AI and must never stop the engine or a tool.
    A summary without a note (older runs) gets one built from its params, with the same warning."""
    try:
        d = json.loads((Path(runs) / "backtests" / name / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    m = d.get("markets")
    markets = {s: x for s, x in m.items() if isinstance(x, dict)} if isinstance(m, dict) else {}
    note = d.get("note")
    if not isinstance(note, str) or not note:
        p = d.get("params") if isinstance(d.get("params"), dict) else {}
        try:
            entry = f", entry {float(p['entry_tmn']) * 100:.2f}% (TMN) / {float(p['entry_usdt']) * 100:.2f}% (USDT)"
        except (KeyError, TypeError, ValueError):
            entry = ""
        days = f" over {p['days']} days" if p.get("days") else ""
        note = (f"1-minute-candle backtest '{name}' of the fair-value rule{days}{entry}, modelled spreads. "
                "Its summary has no next-minute robustness check: treat the numbers as an optimistic upper bound, "
                "not a forecast.")
    return {"note": note, "markets": markets}


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
