"""How durable are the backtest's entry signals? For each entry of the current-policy simulation (no risk rules),
look at the NEXT traded minute of that market: how long after, and what buy_dev it shows. Also re-run the
persistence variant with a looser time limit (next traded minute within 5 / 10 minutes), using bt_check's frames.
"""
import json
import os
import sys

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.argv = ["bt_check.py"]
import io
import contextlib

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    import bt_check as B   # re-runs bt_check (own code), we reuse its frames and simulate()
import numpy as np
import pandas as pd

tr = pd.read_csv("trades_current_policy_no_risk_rules.csv")
rows = []
for _, r in tr.iterrows():
    f = B.frames[r.symbol]
    t = pd.Timestamp(int(r.t), unit="s", tz="UTC")
    i = f.index.get_loc(t)
    if i + 1 < len(f):
        gap = (f.index[i + 1] - t).total_seconds() / 60
        rows.append(dict(symbol=r.symbol, gap_min=gap, bd_entry=f.bd.iloc[i] * 100, bd_next=f.bd.iloc[i + 1] * 100,
                         ret=r.ret))
d = pd.DataFrame(rows)
print("entries:", len(d))
print("next traded minute gap (min): median", d.gap_min.median(), " share <=2 min", round((d.gap_min <= 2).mean() * 100, 1))
print("buy_dev at next traded minute %: median", round(d.bd_next.median(), 3),
      " share still <= -0.7:", round((d.bd_next <= -0.7).mean() * 100, 1),
      " share <= -0.35:", round((d.bd_next <= -0.35).mean() * 100, 1),
      " share >= 0 (gone):", round((d.bd_next >= 0).mean() * 100, 1))
print(d.groupby("symbol").agg(n=("ret", "size"), gap_med=("gap_min", "median"), bd_next_med=("bd_next", "median"),
                              ret_mean=("ret", "mean")).round(3).to_string())

# looser persistence windows
out = {}
for lim in (120, 300, 600):
    # simple re-implementation with a different limit: patch the constant 120 in a copy of the function source
    import inspect, textwrap
    s = inspect.getsource(B.simulate).replace("t - prev <= 120", f"t - prev <= {lim}")
    ns = dict(B.__dict__)
    exec(textwrap.dedent(s), ns)
    t2 = ns["simulate"](B.POL_CUR, risk=True, persist=True)
    out[lim] = {"n": int(len(t2)), "mean": round(t2.ret.mean(), 3) if len(t2) else None,
                "sum": round(t2.ret.sum(), 2) if len(t2) else 0,
                "per_market": t2.symbol.value_counts().to_dict() if len(t2) else {}}
print("persistence variant by max gap to next traded minute (s):", json.dumps(out))
print("candle tails BTC/ETH:", json.dumps({s: B.res["candle_tails"][s] for s in ("BTCTMN", "ETHTMN")}))
