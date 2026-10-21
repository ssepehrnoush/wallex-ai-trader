"""Append-only decision log (JSONL). One line per event: every candidate with the AI's
verdict and the risk manager's reasons, every order, fill, exit, and equity snapshot.
This is the "why did it buy/sell" record the challenge asks for."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class Journal:
    def __init__(self, run_dir: Path):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.dir = run_dir
        self._f = open(run_dir / "decisions.jsonl", "a", encoding="utf-8")

    def log(self, event: str, **data) -> dict:
        rec = {"t": data.pop("t", None) or datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **data}
        self._f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._f.flush()
        return rec

    def close(self):
        self._f.close()


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
