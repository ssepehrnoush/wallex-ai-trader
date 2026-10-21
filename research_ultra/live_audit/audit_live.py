"""Read-only audit of the LIVE run (runs/live_main): why zero trades?

Reads a snapshot of MAIN/runs/live_main/{ticks.csv,decisions.jsonl,state.json} (never writes there),
plus the shadow runs of the same day for a longer "today" sample. Writes live_stats.json next to this file.

    python research_ultra/live_audit/audit_live.py
"""
import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import io
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
LIVE = MAIN / "runs" / "live_main"
OUT = Path(__file__).resolve().parent
LIVE_START = pd.Timestamp("2026-10-21 16:45:00", tz="UTC")
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
FLOOR = 2 * 0.003 + 0.001          # min_entry for TMN
SIZE_TMN = 5_000_000 * 0.006 * 0.4 / (0.009 + 0.003 + 0.0025)   # notional at size_factor 0.4, stop 0.9%
THRESH = [0.003, 0.004, 0.005, 0.006, 0.007]


def read_snapshot(p: Path) -> str:
    raw = p.read_bytes().decode("utf-8", errors="ignore")
    return raw[: raw.rfind("\n") + 1]          # drop a partial last line (file is being appended)


def load_ticks(p: Path) -> pd.DataFrame:
    t = pd.read_csv(io.StringIO(read_snapshot(p)))
    t = t[pd.to_numeric(t.t, errors="coerce").notna()].copy()
    for c in ("t", "bid", "ask", "bid_qty", "ask_qty", "fair", "baseline", "buy_dev", "sell_dev"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t.dropna()
    t["dt"] = pd.to_datetime(t.t, unit="s", utc=True)
    t["spread"] = t.ask / t.bid - 1
    t["prem"] = (t.bid + t.ask) / 2 / t.fair - 1
    t["ask_tmn"] = t.ask * t.ask_qty
    t["bid_tmn"] = t.bid * t.bid_qty
    return t.sort_values(["symbol", "t"]).reset_index(drop=True)


def per_market(t: pd.DataFrame) -> dict:
    out = {}
    for s in SYMS:
        g = t[t.symbol == s]
        if g.empty:
            continue
        dt = g.t.diff().shift(-1).fillna(2.5).clip(upper=10)       # seconds each tick represents
        bd = g.buy_dev
        i_min = bd.idxmin()
        mins = {f"{x * 100:.1f}%": round(float(dt[bd <= -x].sum() / 60), 2) for x in THRESH}
        # number of separate episodes crossing each threshold
        eps = {}
        for x in THRESH:
            below = (bd <= -x).astype(int).to_numpy()
            eps[f"{x * 100:.1f}%"] = int(((below[1:] == 1) & (below[:-1] == 0)).sum() + (below[0] if len(below) else 0))
        out[s] = {
            "ticks": int(len(g)), "hours": round((g.t.max() - g.t.min()) / 3600, 2),
            "buy_dev_min_pct": round(float(bd.min() * 100), 3), "buy_dev_min_at": str(g.loc[i_min, "dt"])[:19],
            "spread_at_min_pct": round(float(g.loc[i_min, "spread"] * 100), 3),
            "buy_dev_p01_pct": round(float(bd.quantile(0.001) * 100), 3),
            "buy_dev_p1_pct": round(float(bd.quantile(0.01) * 100), 3),
            "buy_dev_p5_pct": round(float(bd.quantile(0.05) * 100), 3),
            "buy_dev_p50_pct": round(float(bd.median() * 100), 3),
            "share_buy_dev_positive_pct": round(float((bd > 0).mean() * 100), 1),
            "minutes_below": mins, "episodes_below": eps,
            "spread_median_pct": round(float(g.spread.median() * 100), 3),
            "spread_p90_pct": round(float(g.spread.quantile(0.9) * 100), 3),
            "spread_p99_pct": round(float(g.spread.quantile(0.99) * 100), 3),
            "spread_max_pct": round(float(g.spread.max() * 100), 3),
            "share_spread_over_0.6_pct": round(float((g.spread > 0.006).mean() * 100), 2),
            "baseline_first_pct": round(float(g.baseline.iloc[0] * 100), 3),
            "baseline_last_pct": round(float(g.baseline.iloc[-1] * 100), 3),
            "baseline_min_pct": round(float(g.baseline.min() * 100), 3),
            "baseline_max_pct": round(float(g.baseline.max() * 100), 3),
            "premium_first_pct": round(float(g.prem.iloc[:40].median() * 100), 3),
            "premium_last_pct": round(float(g.prem.iloc[-40:].median() * 100), 3),
            # baseline lag: how far the current premium sits above the rolling median (positive = buy_dev biased up)
            "premium_minus_baseline_median_pct": round(float((g.prem - g.baseline).median() * 100), 3),
            "top_ask_tmn_median": round(float(g.ask_tmn.median())),
            "top_ask_tmn_p10": round(float(g.ask_tmn.quantile(0.1))),
            "share_top_ask_below_size_pct": round(float((g.ask_tmn < SIZE_TMN).mean() * 100), 1),
            "share_top_ask_below_105k_pct": round(float((g.ask_tmn < 105_000).mean() * 100), 1),
            "top_bid_tmn_median": round(float(g.bid_tmn.median())),
            "share_top_bid_below_size_pct": round(float((g.bid_tmn < SIZE_TMN).mean() * 100), 1),
            "first_tick": str(g.dt.min())[:19], "last_tick": str(g.dt.max())[:19],
        }
    return out


def policy_timeline(events: list[dict]) -> list[dict]:
    rows = []
    for e in events:
        if e["event"] == "policy_review":
            rows.append({"t": e["t"], "risk_mode": e.get("risk_mode"), "analyst_model": e.get("analyst_model"),
                         "policies": {s: {k: p[k] for k in ("enabled", "entry_dev", "stop_pct", "max_hold_min", "size_factor")}
                                      for s, p in e["policies"].items()}})
        elif e["event"] == "start":
            rows.append({"t": e["t"], "restart_default_policy": e["policy"]["TMN"]})
    return rows


def gate_walk(t: pd.DataFrame, events: list[dict]) -> dict:
    """Replay the engine's entry gates on every live tick, in code order, with the policy in force at
    that moment (default policy after each start until the first review of that process)."""
    starts = [pd.Timestamp(e["t"]) for e in events if e["event"] == "start"]
    reviews = [(pd.Timestamp(e["t"]), e["policies"]) for e in events if e["event"] == "policy_review"]
    default = next(e["policy"]["TMN"] for e in events if e["event"] == "start")
    res = {}
    for s in SYMS:
        g = t[(t.symbol == s) & (t.dt >= LIVE_START)]
        counts = {"ticks": len(g), "warmup(3 min after start)": 0, "disabled_by_policy": 0,
                  "above_threshold": 0, "below_threshold_but_spread>0.6%": 0, "would_reach_risk.size": 0}
        for dt_, bd, sp in zip(g.dt, g.buy_dev, g.spread):
            st = max([x for x in starts if x <= dt_], default=None)
            if st is None or dt_ < st + pd.Timedelta(minutes=3):
                counts["warmup(3 min after start)"] += 1
                continue
            pol = default
            for rt, pols in reviews:
                if st <= rt <= dt_:
                    pol = pols.get(s, pol)
            if not pol["enabled"]:
                counts["disabled_by_policy"] += 1
                continue
            thr = max(pol["entry_dev"], FLOOR)
            if bd > -thr:
                counts["above_threshold"] += 1
                continue
            if sp > 0.006:
                counts["below_threshold_but_spread>0.6%"] += 1
                continue
            counts["would_reach_risk.size"] += 1
        res[s] = counts
    return res


def main():
    t0 = time.time()
    live = load_ticks(LIVE / "ticks.csv")
    live = live[live.dt >= LIVE_START]
    events = []
    bad = 0
    for line in read_snapshot(LIVE / "decisions.jsonl").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    state = json.loads((LIVE / "state.json").read_text(encoding="utf-8"))
    ev_counts = pd.Series([e["event"] for e in events]).value_counts().to_dict()
    flagged = [e for e in events if e["event"].endswith("_error") or e["event"] in
               ("feed_gap", "blocked", "skip", "entry_unfilled", "exit_unfilled", "dust_left", "stray_order_cancelled",
                "adopted_holding", "position_resized", "window_closed", "stop")]
    eq = [e for e in events if e["event"] == "equity"]
    eq_t = pd.to_datetime([e["t"] for e in eq])
    eq_gaps = [(str(a), str(b), round((b - a).total_seconds())) for a, b in zip(eq_t[:-1], eq_t[1:]) if (b - a).total_seconds() > 90]

    # same-day shadow ticks (TMN markets only) for a longer "today" sample
    shadow = []
    for r in ("shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6"):
        p = MAIN / "runs" / r / "ticks.csv"
        if p.exists():
            x = load_ticks(p)
            shadow.append(x[x.symbol.isin(SYMS)])
    sh = pd.concat(shadow).sort_values(["symbol", "t"]).reset_index(drop=True) if shadow else pd.DataFrame()

    # synthetic rows that the warm start and the MCP tools would pick up (any ticks file with mtime in window)
    test_ticks = load_ticks(MAIN / "runs" / "test_live_paths" / "ticks.csv")
    synthetic = {"rows": len(test_ticks), "first": str(test_ticks.dt.min())[:19], "last": str(test_ticks.dt.max())[:19],
                 "symbols": test_ticks.symbol.value_counts().to_dict(),
                 "max_premium_pct": round(float(test_ticks.prem.max() * 100), 1),
                 "max_sell_dev_pct": round(float(test_ticks.sell_dev.max() * 100), 1)}

    out = {
        "snapshot_utc": str(pd.Timestamp.now(tz="UTC"))[:19],
        "live_ticks_first": str(live.dt.min())[:19], "live_ticks_last": str(live.dt.max())[:19],
        "live_hours": round((live.t.max() - live.t.min()) / 3600, 2),
        "decision_events": ev_counts, "unparseable_lines": bad,
        "flagged_events": flagged, "equity_gaps_over_90s": eq_gaps,
        "state": {k: state.get(k) for k in ("t", "equity_tmn", "return_pct", "drawdown_pct", "halted", "positions", "trades")},
        "stop_flag_exists": (LIVE / "STOP").exists(), "flatten_flag_exists": (LIVE / "FLATTEN").exists(),
        "policy_timeline": policy_timeline(events),
        "size_tmn_at_factor_0.4": round(SIZE_TMN),
        "live_by_market": per_market(live),
        "shadow_today_by_market": per_market(sh) if len(sh) else {},
        "gate_walk_live": gate_walk(live, events),
        "synthetic_test_ticks": synthetic,
    }
    (OUT / "live_stats.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    print(f"live ticks {out['live_ticks_first']} -> {out['live_ticks_last']} ({out['live_hours']} h), events {ev_counts}, bad lines {bad}")
    print("flagged events:", len(flagged), "equity gaps >90s:", eq_gaps)
    print("STOP:", out["stop_flag_exists"], "FLATTEN:", out["flatten_flag_exists"])
    hdr = f"{'market':8} {'min':>7} {'p1':>7} {'p50':>7} {'%>0':>5} | min<0.4 <0.5 <0.6 <0.7 | sprd med p99 >0.6% | base first->last | prem-base | topask med"
    for name, d in (("LIVE (since 16:45)", out["live_by_market"]), ("SHADOW today (14:00-16:25)", out["shadow_today_by_market"])):
        print("\n" + name)
        print(hdr)
        for s, m in d.items():
            mb = m["minutes_below"]
            print(f"{s:8} {m['buy_dev_min_pct']:7.3f} {m['buy_dev_p1_pct']:7.3f} {m['buy_dev_p50_pct']:7.3f} {m['share_buy_dev_positive_pct']:5.1f} | "
                  f"{mb['0.4%']:6.2f} {mb['0.5%']:5.2f} {mb['0.6%']:5.2f} {mb['0.7%']:5.2f} | {m['spread_median_pct']:5.3f} {m['spread_p99_pct']:5.3f} {m['share_spread_over_0.6_pct']:5.2f} | "
                  f"{m['baseline_first_pct']:6.3f}->{m['baseline_last_pct']:6.3f} | {m['premium_minus_baseline_median_pct']:6.3f} | {m['top_ask_tmn_median']:>12,}")
    print("\ndepth vs the 827k order size, and sample span:")
    for name, d in (("live", out["live_by_market"]), ("shadow", out["shadow_today_by_market"])):
        for s_, m in d.items():
            print(f"  {name:6} {s_:8} {m['first_tick']} -> {m['last_tick']} ticks={m['ticks']} topask<size {m['share_top_ask_below_size_pct']}% "
                  f"topask<105k {m['share_top_ask_below_105k_pct']}% topbid med {m['top_bid_tmn_median']:,} topbid<size {m['share_top_bid_below_size_pct']}%")
    print("\ngate walk (live):")
    for s, c in out["gate_walk_live"].items():
        print(f"  {s:8} {c}")
    print("synthetic test ticks:", synthetic)
    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
