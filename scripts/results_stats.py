"""Numbers behind RESULTS.md. Reads recorded files only; makes no network call and writes nothing
outside this repo.

Sources (all read-only):
  MAIN/runs/shadow_v1..v6, MAIN/runs/live_main   ticks.csv, decisions.jsonl, state.json
  MAIN/runs/backtests/*                           summaries of the existing 7-day backtests
  WT/research_ultra/*                             outputs of this session's measurement agents

  python scripts/results_stats.py [--main ../wallex-bot] [--cutoff 2026-10-21T18:50:00]

live_main is still being appended: the script reads a snapshot, skips a partial last line, and
records the snapshot time. --cutoff (UTC) freezes the live numbers at a fixed moment.
Writes docs/results_stats.json.
"""
import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

WT = Path(__file__).resolve().parent.parent
RU = WT / "research_ultra"
SHADOW_RUNS = ["shadow_v1", "shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6"]
LIVE_RUN = "live_main"
TMN8 = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
MAJORS = TMN8[:5]
# walk-forward split used by the measurement agents: usable window 10-14 17:41 .. 10-21 14:04 UTC,
# first 4 days vs the rest
SPLIT = pd.Timestamp("2026-10-18 17:41", tz="UTC")


def read_jsonl(p: Path, cutoff=None):
    out = []
    if not p.exists():
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue                      # partial last line of a file still being written
            if cutoff is not None and pd.Timestamp(d["t"]) > cutoff:
                continue
            out.append(d)
    return out


def read_ticks(p: Path, cutoff=None):
    t = pd.read_csv(p, on_bad_lines="skip", dtype={"symbol": str})
    for c in ["t", "bid", "ask", "bid_qty", "ask_qty", "fair", "baseline", "buy_dev", "sell_dev"]:
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t.dropna(subset=["t", "bid", "ask"])
    t = t[t.symbol.str.len().between(5, 12)]
    if cutoff is not None:
        t = t[t.t <= cutoff.timestamp()]
    return t


def utc(x):
    return pd.Timestamp(x, unit="s", tz="UTC").strftime("%Y-%m-%d %H:%M:%S")


def stats(r: pd.Series):
    """r = net % per trade."""
    if len(r) == 0:
        return {"n": 0, "mean_pct": None, "sum_pct": 0.0, "win_pct": None}
    return {"n": int(len(r)), "mean_pct": round(float(r.mean()), 3), "sum_pct": round(float(r.sum()), 2),
            "win_pct": round(float((r > 0).mean() * 100), 1)}


def trade_table(df: pd.DataFrame, ret="ret_pct", why="why", tcol="t"):
    df = df.copy()
    df[tcol] = pd.to_datetime(df[tcol], utc=True)
    out = {"all": stats(df[ret]) | {"exits": df[why].value_counts().to_dict()},
           "days1_4": stats(df[df[tcol] < SPLIT][ret]), "days5_7": stats(df[df[tcol] >= SPLIT][ret]),
           "per_market": {}}
    for s in TMN8:
        g = df[df.symbol == s]
        out["per_market"][s] = stats(g[ret]) | {"exits": g[why].value_counts().to_dict()}
    return out


def tool_server(call):
    if call.get("server"):
        return call["server"]
    return "wallex-official" if str(call.get("tool", "")).startswith("wallex_") else "engine"


def run_summary(run_dir: Path, cutoff=None):
    ticks = read_ticks(run_dir / "ticks.csv", cutoff)
    dec = read_jsonl(run_dir / "decisions.jsonl", cutoff)
    ev = Counter(d["event"] for d in dec)
    reviews = [d for d in dec if d["event"] == "policy_review"]
    calls = Counter()
    per_review = []
    disabled_since = {}
    for r in reviews:
        tc = r.get("analyst_tool_calls") or []
        c = Counter(tool_server(x) for x in tc)
        calls.update(c)
        pols = r.get("policies") or {}
        off = sorted(m for m, p in pols.items() if not p.get("enabled", True))
        disabled_since = {m: disabled_since.get(m, r["t"]) for m in off}      # start of each market's current "off" streak
        per_review.append({"t": r["t"], "risk_mode": r.get("risk_mode"), "model": r.get("analyst_model"),
                           "tool_calls": len(tc), "by_server": dict(c), "disabled": off})
    # the policy in force at the cutoff is the last review's (state.json is read now, so it can be newer than the cutoff)
    policy_at_cutoff = None
    if reviews:
        policy_at_cutoff = {"from_review_utc": reviews[-1]["t"], "disabled_since": disabled_since,
                            "policies": {m: {k: p.get(k) for k in ("enabled", "entry_dev", "size_factor")}
                                         for m, p in (reviews[-1].get("policies") or {}).items()}}
    entries = [d for d in dec if d["event"] == "entry"]
    exits = [d for d in dec if d["event"] == "exit"]
    eq = [d for d in dec if d["event"] == "equity"]
    starts = [d for d in dec if d["event"] == "start"]
    by_sym = {}
    for s, g in ticks.groupby("symbol"):
        bd = g.buy_dev.dropna()
        i = bd.idxmin() if len(bd) else None
        by_sym[s] = {"ticks": int(len(g)),
                     "min_buy_dev_pct": round(float(bd.min() * 100), 3) if len(bd) else None,
                     "min_at_utc": utc(g.loc[i, "t"]) if i is not None else None,
                     "ticks_le_0.7pct": int((bd <= -0.007).sum()),
                     "ticks_le_0.5pct": int((bd <= -0.005).sum()),
                     "median_spread_pct": round(float(((g.ask / g.bid - 1) * 100).median()), 3)}
    first, last = (float(ticks.t.min()), float(ticks.t.max())) if len(ticks) else (None, None)
    out = {
        "first_tick_utc": utc(first) if first else None,
        "last_tick_utc": utc(last) if last else None,
        "tick_minutes": round((last - first) / 60, 1) if first else 0,
        "tick_rows": int(len(ticks)),
        "markets": sorted(by_sym),
        "events": dict(ev),
        "starts": [s["t"] for s in starts],
        "start_entry_dev_tmn": [s.get("policy", {}).get("TMN", {}).get("entry_dev") for s in starts],
        "reviews": per_review,
        "policy_at_cutoff": policy_at_cutoff,
        "risk_modes": dict(Counter(r["risk_mode"] for r in per_review)),
        "tool_calls_by_server": dict(calls),
        "tool_calls_total": int(sum(calls.values())),
        "entries": [{k: e.get(k) for k in ("t", "symbol", "price", "buy_dev_pct", "why_fa")} for e in entries],
        "exits": [{k: x.get(k) for k in ("t", "symbol", "reason", "ret_pct", "pnl_tmn", "held_s")} for x in exits],
        "equity_marks": len(eq),
        "equity_first": eq[0]["equity_tmn"] if eq else None,
        "equity_last": eq[-1]["equity_tmn"] if eq else None,
        "return_last_pct": eq[-1]["return_pct"] if eq else None,
        "max_drawdown_pct": max((d.get("drawdown_pct", 0) for d in eq), default=None),
        "min_return_pct": min((d.get("return_pct", 0) for d in eq), default=None),
        "max_return_pct": max((d.get("return_pct", 0) for d in eq), default=None),
        "equity_series": [(d["t"], d["equity_tmn"]) for d in eq],
        "by_symbol": by_sym,
    }
    st = run_dir / "state.json"
    if st.exists():
        try:
            s = json.loads(st.read_text(encoding="utf-8"))
            out["state"] = {k: s.get(k) for k in ("t", "mode", "equity_tmn", "return_pct", "drawdown_pct",
                                                  "halted", "trades")}
            out["state"]["open_positions"] = len(s.get("positions") or {})
            out["state"]["policies"] = {m: {k: p.get(k) for k in ("enabled", "entry_dev", "stop_pct",
                                                                  "max_hold_min", "size_factor")}
                                        for m, p in (s.get("policies") or {}).items()}
        except ValueError:
            pass
    return out


def backtests(main: Path):
    bt = main / "runs" / "backtests"
    res = {}
    # 1) existing sweep: 5 majors (+ USDT), original exit rule, shadow_v1 spreads
    sweep = {}
    for th, d in [(0.5, "fv_sweep_0.005_0.003"), (0.6, "fv_sweep_0.006_0.0035"),
                  (0.7, "fv_sweep_0.007_0.004"), (0.8, "fv_sweep_0.008_0.005")]:
        tr = pd.read_csv(bt / d / "trades.csv")
        tmn = tr[tr.symbol.isin(MAJORS)]
        sweep[str(th)] = {"tmn_majors": stats(tmn.ret_pct) | {"exits": tmn.why.value_counts().to_dict()},
                          "per_market": {s: stats(tmn[tmn.symbol == s].ret_pct) for s in MAJORS},
                          "source": f"runs/backtests/{d}/trades.csv"}
    res["sweep_majors_existing"] = sweep
    # 2) 8-market sweep, live exit rule, today's tick spreads (independent verifier of sizing_depth)
    s8 = {}
    vi = RU / "sizing_depth" / "verify_independent"
    for th in ["0.003", "0.004", "0.005", "0.006", "0.007", "0.008"]:
        p = vi / f"v_trades_base_ff_{th}.csv"
        if p.exists():
            tr = pd.read_csv(p)
            tr["ret_pct"] = tr["ret"] * 100
            s8[str(round(float(th) * 100, 1))] = stats(tr.ret_pct) | {
                "exits": tr.why.value_counts().to_dict(),
                "source": f"research_ultra/sizing_depth/verify_independent/{p.name}"}
    res["sweep_8_live_rule"] = s8
    # 3) per market at 0.7%: existing method (orig exits, shadow_v1 spreads) and live rule (tick spreads)
    so = RU / "sizing_depth" / "out"
    res["at_0.7_existing_method"] = trade_table(pd.read_csv(so / "bt_trades_orig_sv1_ff.csv")) | {
        "source": "research_ultra/sizing_depth/out/bt_trades_orig_sv1_ff.csv"}
    res["at_0.7_live_rule"] = trade_table(pd.read_csv(so / "bt_trades_live_tick_ff.csv")) | {
        "source": "research_ultra/sizing_depth/out/bt_trades_live_tick_ff.csv"}
    # 4) PEPE BNB ARB at 0.6% from the original extra-markets run
    ex = json.loads((bt / "fv_extra_tmn" / "summary.json").read_text(encoding="utf-8"))
    res["extra_0.6"] = {s: ex["markets"][s] for s in ("PEPETMN", "BNBTMN", "ARBTMN")}
    res["sweep_spreads_shadow_v1_pct"] = json.loads(
        (bt / "fv_sweep_0.007_0.004" / "summary.json").read_text(encoding="utf-8"))["spreads_from_live"]
    # 5) rejected strategies
    rs = json.loads((bt / "rules_summary.json").read_text(encoding="utf-8"))
    res["rules"] = {k: rs.get(k) for k in ("start", "end", "days", "return_pct", "max_drawdown_pct", "trades",
                                           "win_rate_pct", "avg_trade_ret_pct", "profit_factor", "by_reason")}
    wk = json.loads((bt / "wick_summary.json").read_text(encoding="utf-8"))
    wick = defaultdict(lambda: {"n": 0, "sum_pct": 0.0, "stops": 0, "markets_neg": 0, "markets": 0})
    for m, v in wk.items():
        for th, r in v.items():
            if th == "half_spread_pct":
                continue
            w = wick[th]
            w["n"] += r["n"]
            w["sum_pct"] += r["sum_pct"]
            w["stops"] += r["stops"]
            w["markets"] += 1
            w["markets_neg"] += int(r["sum_pct"] < 0)
    res["wick"] = {th: {**w, "sum_pct": round(w["sum_pct"], 2),
                        "mean_pct": round(w["sum_pct"] / w["n"], 3) if w["n"] else None,
                        "sum_pct_without_ARB": round(w["sum_pct"] - wk.get("ARBTMN", {}).get(th, {}).get("sum_pct", 0), 2)}
                   for th, w in sorted(wick.items())}
    res["wick_markets"] = sorted(wk)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default=str(WT.parent / "wallex-bot"))
    ap.add_argument("--cutoff", default="", help="UTC time to freeze the live snapshot")
    ap.add_argument("--out", default=str(WT / "docs" / "results_stats.json"))
    a = ap.parse_args()
    main_dir = Path(a.main)
    cutoff = pd.Timestamp(a.cutoff, tz="UTC") if a.cutoff else None
    try:
        main_label = Path(os.path.relpath(main_dir, WT)).as_posix()
    except ValueError:
        main_label = main_dir.name
    res = {"main": main_label, "cutoff": a.cutoff or None, "runs": {}}
    for r in SHADOW_RUNS + [LIVE_RUN]:
        res["runs"][r] = run_summary(main_dir / "runs" / r, cutoff if r == LIVE_RUN else None)
    res["backtests"] = backtests(main_dir)
    lv = res["runs"][LIVE_RUN]
    res["live_snapshot_utc"] = lv["last_tick_utc"]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # console summary
    print("live snapshot:", lv["last_tick_utc"], "| starts:", lv["starts"])
    print("live: minutes of ticks %.1f, events %s" % (lv["tick_minutes"], lv["events"]))
    print("live: equity first/last %s/%s return %s maxDD %s state %s" % (
        lv["equity_first"], lv["equity_last"], lv["return_last_pct"], lv["max_drawdown_pct"], lv.get("state")))
    print("live: reviews", len(lv["reviews"]), lv["risk_modes"], "calls", lv["tool_calls_by_server"])
    for r in lv["reviews"]:
        print("   ", r)
    for s, v in lv["by_symbol"].items():
        print("   ", s, v)
    for r in SHADOW_RUNS:
        x = res["runs"][r]
        print(r, x["first_tick_utc"], x["last_tick_utc"], x["tick_minutes"], "min", x["tick_rows"], "rows",
              len(x["markets"]), "mkts", x["events"], "ret", x["return_last_pct"], "calls",
              x["tool_calls_by_server"], "modes", x["risk_modes"])
        for e in x["exits"]:
            print("    exit", e)
        mins = {s: v["min_buy_dev_pct"] for s, v in x["by_symbol"].items()}
        print("    min buy_dev", mins)
    b = res["backtests"]
    print("sweep majors:", {k: v["tmn_majors"] for k, v in b["sweep_majors_existing"].items()})
    print("sweep 8 live rule:", {k: {kk: v[kk] for kk in ("n", "mean_pct", "sum_pct", "win_pct", "exits")}
                                 for k, v in b["sweep_8_live_rule"].items()})
    for k in ("at_0.7_existing_method", "at_0.7_live_rule"):
        print(k, b[k]["all"], "d1-4", b[k]["days1_4"], "d5-7", b[k]["days5_7"])
        for s, v in b[k]["per_market"].items():
            print("    ", s, v)
    print("extra 0.6:", b["extra_0.6"])
    print("rules:", b["rules"])
    print("wick:", b["wick"])


if __name__ == "__main__":
    main()
