import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json
import pandas as pd
from common import OUT, MAIN_MKTS, SPREADS
ev = json.load(open(OUT / "event_study.json"))
fe = json.load(open(OUT / "final_eval.json"))
te = json.load(open(OUT / "tick_event_study.json"))
W = pd.read_pickle(OUT / "grid_pooled.pkl")
pick = lambda k: {x: ev["pooled"][k][x] for x in ["n", "rb_mean%", "rw_mean%", "dev_mean%", "net1_mean%", "net5_mean%", "net10_mean%", "net30_mean%",
                                                  "gw5_mean%", "gw30_mean%", "P(gw5>0.6%)", "P(gw30>0.6%)", "win5", "win30"]}
grid = {}
for off in [0, 1, 2]:
    m = W[W[f"n_is_o{off}"] >= 30]
    grid[f"off{off}"] = {"configs_with_n_is>=30": int(len(m)), "IS_mean>0": int((m[f"mean_is_o{off}"] > 0).sum()),
                         "best_IS_mean%": round(float(m[f"mean_is_o{off}"].max()), 3), "median_IS_mean%": round(float(m[f"mean_is_o{off}"].median()), 3)}
m = W[W.n_is_o1 >= 10]
grid["off1_n_is>=10"] = {"configs": int(len(m)), "IS_mean>0": int((m.mean_is_o1 > 0).sum()),
                         "IS>0_and_OOS>0": int(((m.mean_is_o1 > 0) & (m.mean_oos_o1 > 0)).sum())}
res = {
    "family": "global lead-lag momentum (Binance jump up, Wallex not yet followed -> buy Wallex, exit on catch-up/stop/time)",
    "verdict": "NOT VIABLE",
    "markets": MAIN_MKTS, "half_spread_used": {s: SPREADS[s] / 2 for s in MAIN_MKTS},
    "excluded": {"POLTMN": "median live spread 0.96% > 0.6%", "SANDTMN": "no spread in json; tick median 0.56%, only 11.5% minutes traded; event study net -0.6..-1.1% (reported in event_study.json only)"},
    "conventions": {"fees": "taker 0.3% both sides, maker 0.25% for limit take-profit", "entry": "close of minute t+off times (1+h); off=1 primary, off=2 one minute later, off=0 signal minute (optimistic)",
                    "exit": "decision on close j, filled close j+1 times (1-h); time exit at entry+N", "split": "IS 2026-09-22 07:00 to 10-12 07:00 (20 d), OOS 10-12 to 10-22 07:00 (10 d)",
                    "portfolio": "1 position per market, max 3 open"},
    "event_study_pooled_8_markets": {
        "lagging_k5_X1.0_off0": pick("lagging|k5|X0.01|off0"), "lagging_k5_X1.0_off1": pick("lagging|k5|X0.01|off1"),
        "lagging_k3_X0.8_off1": pick("lagging|k3|X0.008|off1"), "lagging_k2_X0.5_off1": pick("lagging|k2|X0.005|off1"),
        "all_k3_X0.5_off1": pick("all|k3|X0.005|off1"), "lagging_k5_X1.5_off0": pick("lagging|k5|X0.015|off0"),
        "lagging_k5_X1.5_off1": pick("lagging|k5|X0.015|off1"),
        "note": "net = taker in/out incl. spread; gw = Wallex last-price gain after entry; 'lagging' = Wallex k-min return <= half of Binance's"},
    "grid_search": {"size": int(len(W)), "dims": "k{1,2,3,5} x X{0.3,0.5,0.7,1.0,1.5%} x lag{none,half,zero,dev<=-0.4%,dev<=-0.7%} x traded{0,1} x persist{0,1} x exit{catchup dev>=0 / >=-0.1% + fees covered, maker TP 0.3% / 0.6%} x stop{0.5,0.9,1.5%,none} x N{3,5,10,20,30}",
                    "summary": grid},
    "rule_A_selected": fe["A_best_IS_n30"],
    "rule_B_exploratory_small_n": fe["B_best_IS_n10"],
    "rule_B_note": "IS +0.335%/trade rests on one +5.65% ARBTMN trade (2026-10-07 02:08, dev -6.2%); without it IS mean -0.045%",
    "tick_replay": {k: {kk: vv for kk, vv in v.items() if kk != "trades"} if isinstance(v, dict) else v for k, v in fe["tick_replay"].items()},
    "tick_replay_rule_A_trade": fe["tick_replay"]["A_best_IS_n30|next_tick"].get("trades"),
    "tick_event_study": te["event_study"],
}
json.dump(res, open(OUT / "results.json", "w"), indent=1, default=str)
print("ok", OUT / "results.json")
