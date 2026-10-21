"""Exit-rule grid with walk-forward on the 7-day 1-minute candles (8 live TMN markets).

  grid: stop {0.6, 0.9, 1.2, 1.5}% x hold {10, 15, 25, 45} min x exit threshold X {0, 0.05, 0.1, 0.2, 0.3}%
        (X = exit_slack; X in {0.1, 0.2, 0.3} is the "partial reversion" target: sell when sell_dev >= -X,
        still only if bid >= entry x (1 + 2 x taker + 0.05%)). 80 configs. Current live = (0.9, 25, 0.05).
  fold A (the real test): choose on days 1-4 (before 2026-10-18 14:05 UTC), evaluate on days 5-7.
  fold B (robustness):   choose on days 5-7, evaluate on days 1-4.
  variants: stop checked on the 1-min close (primary) or on the 1-min low (pessimistic); spreads x1 or x1.5.

  python research_ultra/exit_rules/grid_wf.py
"""
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, SYMS, load_markets, per_market, signals, simulate, stats, suffix  # noqa: E402

STOPS = [0.006, 0.009, 0.012, 0.015]
HOLDS = [10, 15, 25, 45]
SLACKS = [0.0, 0.0005, 0.001, 0.002, 0.003]
CUR = (0.009, 25, 0.0005)
SPLIT = int(pd.Timestamp("2026-10-18 14:05", tz="UTC").timestamp())
rng = np.random.default_rng(7)


def key(c):
    return f"stop{c[0]*100:.1f}_hold{c[1]}_x{c[2]*100:.2f}"


def paired_boot(a: pd.DataFrame, b: pd.DataFrame, n=10000):
    """a = variant trades, b = baseline trades. Pair on (symbol, entry minute); a trade only one side takes
    counts as 0 on the other. Returns observed sum diff (% pts) and bootstrap 90% CI + P(diff>0)."""
    ka = a.set_index(["symbol", "t_entry"]).ret_pct if len(a) else pd.Series(dtype=float)
    kb = b.set_index(["symbol", "t_entry"]).ret_pct if len(b) else pd.Series(dtype=float)
    u = ka.index.union(kb.index)
    d = (ka.reindex(u).fillna(0) - kb.reindex(u).fillna(0)).to_numpy()
    if len(d) == 0:
        return {"diff_sum_pct": 0.0, "n_pairs": 0}
    bs = rng.choice(d, size=(n, len(d)), replace=True).sum(1)
    return {"diff_sum_pct": round(float(d.sum()), 3), "n_pairs": int(len(d)), "n_changed": int((np.abs(d) > 1e-9).sum()),
            "ci90": [round(float(np.quantile(bs, 0.05)), 3), round(float(np.quantile(bs, 0.95)), 3)],
            "p_gt0": round(float((bs > 0).mean()), 3)}


def run_variant(stop_mode: str, spread_mult: float):
    mk = load_markets(spread_mult=spread_mult)
    t_first = min(int(m.t[0]) for m in mk.values())
    t_last = max(int(m.t[-1]) for m in mk.values())
    sig_is, sig_oos = signals(mk, t1=SPLIT), signals(mk, t0=SPLIT)
    configs = list(itertools.product(STOPS, HOLDS, SLACKS))
    rows, trades = [], {}
    for c in configs:
        tis = simulate(mk, sig_is, *c, stop_mode=stop_mode, t_end=SPLIT - 60)     # IS exits cannot peek past the split
        too = simulate(mk, sig_oos, *c, stop_mode=stop_mode)
        trades[c] = (tis, too)
        si, so = stats(tis), stats(too)
        rows.append({"cfg": key(c), "stop": c[0] * 100, "hold": c[1], "x": c[2] * 100,
                     **{f"is_{k}": v for k, v in si.items() if k != "exits"},
                     **{f"oos_{k}": v for k, v in so.items() if k != "exits"},
                     "is_exits": json.dumps(si["exits"]), "oos_exits": json.dumps(so["exits"])})
    g = pd.DataFrame(rows)
    out = {"stop_mode": stop_mode, "spread_mult": spread_mult,
           "half_spreads_pct": {s: round(m.hs * 100, 4) for s, m in mk.items()},
           "data_from": str(pd.Timestamp(t_first, unit="s", tz="UTC")), "split": str(pd.Timestamp(SPLIT, unit="s", tz="UTC")),
           "data_to": str(pd.Timestamp(t_last, unit="s", tz="UTC")),
           "signal_minutes": {"is": len(sig_is), "oos": len(sig_oos)}}
    for fold, sel_col, ev in (("A_choose_d1-4_test_d5-7", "is", "oos"), ("B_choose_d5-7_test_d1-4", "oos", "is")):
        # ties (common: stops rarely trigger, so several stops give identical % results) go to the config
        # closest to the current one, so a tie can never "choose" a change
        g["_dist"] = (abs(g.stop - CUR[0] * 100) / 0.3 + abs(np.log(g.hold / CUR[1])) / np.log(45 / 25)
                      + abs(g.x - CUR[2] * 100) / 0.05)
        gg = g.sort_values([f"{sel_col}_sum_pct", "_dist"], ascending=[False, True])
        best = gg.iloc[0]
        bc = (best.stop / 100, int(best.hold), round(best.x / 100, 6))
        cur_row = g[g.cfg == key(CUR)].iloc[0]
        ev_idx = 1 if ev == "oos" else 0
        bt, ct = trades[bc][ev_idx], trades[CUR][ev_idx]
        # rank of the chosen config among all 80 on the evaluation half
        g_ev = g.sort_values(f"{ev}_sum_pct", ascending=False).reset_index(drop=True)
        rank = int(g_ev.index[g_ev.cfg == key(bc)][0]) + 1
        # by TMN P&L as an alternative selection objective
        best_tmn = g.sort_values(f"{sel_col}_pnl_tmn", ascending=False).iloc[0]
        out[fold] = {
            "chosen": key(bc), "chosen_select_half": {k: best[f"{sel_col}_{k}"] for k in ("n", "mean_pct", "sum_pct", "win_pct", "pnl_tmn")},
            "current_select_half": {k: cur_row[f"{sel_col}_{k}"] for k in ("n", "mean_pct", "sum_pct", "win_pct", "pnl_tmn")},
            "chosen_eval": stats(bt), "current_eval": stats(ct),
            "chosen_eval_per_market": per_market(bt), "current_eval_per_market": per_market(ct),
            "paired_bootstrap_eval_chosen_minus_current": paired_boot(bt, ct),
            "chosen_rank_on_eval_of_80": rank,
            "eval_best_cfg": g_ev.cfg.iloc[0], "eval_best_sum_pct": float(g_ev[f"{ev}_sum_pct"].iloc[0]),
            "current_rank_on_eval_of_80": int(g_ev.index[g_ev.cfg == key(CUR)][0]) + 1,
            "share_of_80_beating_current_on_eval": round(float((g[f"{ev}_sum_pct"] > cur_row[f"{ev}_sum_pct"]).mean()), 3),
            "chosen_by_tmn_pnl": best_tmn.cfg,
            "chosen_by_tmn_pnl_eval_pnl_tmn": int(best_tmn[f"{ev}_pnl_tmn"]),
        }
    # one-at-a-time marginal effects around the current config, both halves
    marg = {}
    for name, vals, pos in (("stop", STOPS, 0), ("hold", HOLDS, 1), ("x", SLACKS, 2)):
        marg[name] = {}
        for v in vals:
            c = list(CUR); c[pos] = v; c = tuple(c)
            r = g[g.cfg == key(c)].iloc[0]
            marg[name][str(v * 100 if name != "hold" else v)] = {h: {k: r[f"{h}_{k}"] for k in ("n", "mean_pct", "sum_pct", "win_pct", "pnl_tmn")} | {"exits": json.loads(r[f"{h}_exits"])} for h in ("is", "oos")}
    out["marginal_around_current"] = marg
    tag = f"{stop_mode}_sp{spread_mult}{suffix()}"
    g = g.drop(columns=["_dist"])
    g.to_csv(OUT / f"grid_{tag}.csv", index=False)
    pd.concat([trades[CUR][0].assign(half="is"), trades[CUR][1].assign(half="oos")]).to_csv(OUT / f"trades_current_{tag}.csv", index=False)
    bcA = out["A_choose_d1-4_test_d5-7"]["chosen"]
    cA = [c for c in configs if key(c) == bcA][0]
    pd.concat([trades[cA][0].assign(half="is"), trades[cA][1].assign(half="oos")]).to_csv(OUT / f"trades_chosenA_{tag}.csv", index=False)
    return out


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    return o


if __name__ == "__main__":
    res = {}
    for sm, spm in (("close", 1.0), ("low", 1.0), ("close", 1.5)):
        r = run_variant(sm, spm)
        res[f"{sm}_sp{spm}"] = r
        for fold in ("A_choose_d1-4_test_d5-7", "B_choose_d5-7_test_d1-4"):
            f = r[fold]
            print(f"[{sm} sp{spm}] {fold}: chosen {f['chosen']} | select-half chosen {f['chosen_select_half']} vs current {f['current_select_half']}")
            print(f"    eval chosen {f['chosen_eval']}\n    eval current {f['current_eval']}\n    boot {f['paired_bootstrap_eval_chosen_minus_current']}"
                  f" | chosen rank {f['chosen_rank_on_eval_of_80']}/80, current rank {f['current_rank_on_eval_of_80']}/80,"
                  f" share beating current {f['share_of_80_beating_current_on_eval']}, eval-best {f['eval_best_cfg']} {f['eval_best_sum_pct']}")
    (OUT / f"grid_wf_results{suffix()}.json").write_text(json.dumps(jsonable(res), indent=1), encoding="utf-8")
