"""Live ticks: how long do cheap moments last, consistent 10-min episode count, and what a buy at the ask
would have earned afterwards (live exit rule, taker 0.3% both legs). Own code."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from v_depth import load

OUT = Path(__file__).resolve().parent


def main():
    d = load()
    res = {}
    for thr in [0.003, 0.004, 0.005]:
        runs, eps10 = [], 0
        sims = []
        for s, g in d.groupby("symbol"):
            g = g.reset_index(drop=True)
            t, bd = g.t.to_numpy(), g.buy_dev.to_numpy()
            m = bd <= -thr
            last_cheap = -1e18
            i = 0
            while i < len(g):
                if m[i]:
                    j = i
                    while j + 1 < len(g) and m[j + 1] and t[j + 1] - t[j] <= 10:
                        j += 1
                    runs.append(t[j] - t[i] + 2.5)
                    if t[i] - last_cheap > 600:
                        eps10 += 1
                        # simulate a buy at the ask of tick i, live exit rule
                        e = g.ask[i]
                        hold = 15 * 60 if s == "ARBTMN" else 25 * 60
                        k, why = i + 1, "end"
                        while k < len(g):
                            b = g.bid[k]
                            if b <= e * 0.991: why = "stop"; break
                            if g.sell_dev[k] >= -0.0005 and b >= e * 1.0065: why = "target"; break
                            if t[k] - t[i] >= hold: why = "time"; break
                            k += 1
                        k = min(k, len(g) - 1)
                        px = g.bid[k] * (0.997 if why == "stop" else 1.0)
                        r = px * 0.997 / (e * 1.003) - 1
                        gross_best = (g.bid[i + 1:k + 1].max() / e - 1) if k > i else np.nan
                        sims.append({"symbol": s, "t": str(pd.to_datetime(t[i], unit="s")), "buy_dev_pct": round(bd[i] * 100, 3),
                                     "ret_net_pct": round(r * 100, 3), "why": why, "held_s": round(t[k] - t[i]),
                                     "best_bid_vs_entry_pct": round(gross_best * 100, 3)})
                    last_cheap = t[j]
                    i = j + 1
                else:
                    i += 1
        runs = np.array(runs)
        S = pd.DataFrame(sims)
        res[f"{thr*100:.1f}%"] = {"runs": int(len(runs)), "run_seconds_median": float(np.median(runs)) if len(runs) else None,
                                  "share_runs_le_5s": round(float((runs <= 5).mean()), 3) if len(runs) else None,
                                  "episodes_10min": eps10,
                                  "sim_mean_net_pct": round(float(S.ret_net_pct.mean()), 3) if len(S) else None,
                                  "sim_mean_best_bid_vs_entry_pct": round(float(S.best_bid_vs_entry_pct.mean()), 3) if len(S) else None,
                                  "sims": S.to_dict("records")}
    (OUT / "v_ticks_persist.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    for k, v in res.items():
        print(k, {kk: vv for kk, vv in v.items() if kk != "sims"})
        for r in v["sims"]:
            print("   ", r)


if __name__ == "__main__":
    main()
