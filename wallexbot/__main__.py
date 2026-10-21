"""Command line.

  python -m wallexbot shadow [--minutes N] [--run NAME] [--no-ai]   paper trading on the live market
  python -m wallexbot live --i-understand-real-money [--run NAME]   real orders (hackathon account only)
  python -m wallexbot status [--run NAME]                           print the latest state.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from .config import RUNS, load_config, load_secrets


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wallexbot")
    ap.add_argument("cmd", choices=["shadow", "live", "status", "check"])
    ap.add_argument("--minutes", type=float, default=None)
    ap.add_argument("--run", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--i-understand-real-money", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config(a.config).raw
    if a.no_ai:
        cfg["ai"]["enabled"] = False
    run = a.run or f"{a.cmd}_{datetime.now(timezone.utc):%Y%m%d_%H%M}"

    if a.cmd == "check":
        # read-only pre-flight: which account does this key control, and is it the hackathon one?
        from .client import WallexClient
        s = load_secrets()
        c = WallexClient(s.wallex_key, s.wallex_subaccount or None)
        bal = c.balances()
        nonzero = {k: v[0] for k, v in bal.items() if v[0] > 0}
        fees = c.fees()
        live = cfg["fairvalue"].get("live_symbols", cfg["fairvalue"]["symbols"])
        print(json.dumps({"key_marked_as": s.wallex_key_account, "subaccount_header": bool(s.wallex_subaccount),
                          "nonzero_balances": nonzero, "open_orders": len(c.open_orders()),
                          "fees": {m: fees.get(m) for m in live}}, ensure_ascii=False, indent=1))
        return

    if a.cmd == "status":
        p = RUNS / (a.run or sorted(d.name for d in RUNS.iterdir() if (d / "state.json").exists())[-1]) / "state.json"
        print(p.read_text(encoding="utf-8"))
        return

    if a.cmd == "live":
        s = load_secrets()
        if not a.i_understand_real_money:
            sys.exit("refusing: live mode needs --i-understand-real-money")
        if s.wallex_key_account != "hackathon":
            sys.exit("refusing: WALLEX_KEY_ACCOUNT is not 'hackathon'. The key in .env belongs to the personal "
                     "main account; create a key on the hackathon trade-credit account and set WALLEX_KEY_ACCOUNT=hackathon.")
        if not s.wallex_key:
            sys.exit("refusing: WALLEX_API_KEY is empty")

    from .engine import Engine
    eng = Engine(cfg, "live" if a.cmd == "live" else "shadow", run)
    print(f"run dir: {eng.run_dir}", flush=True)
    eng.run(a.minutes)


if __name__ == "__main__":
    main()
