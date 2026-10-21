"""Audit 4: author's split (choose days 1-4, test 5-7), author's spreads; variants re-selected under each fill model.
Fill models: same close (author) / next traded minute OPEN print / next traded minute CLOSE; all hours and daytime-only."""
import json
import numpy as np
import pandas as pd
import audit_sim as S
D = S.D
R = json.loads((S.Path(__file__).resolve().parent.parent / "out" / "results_main.json").read_text(encoding="utf-8"))
sp_author = {s: v / 100 for s, v in R["params"]["spreads_pct"].items()}
THRS = [0.007, 0.008, 0.009, 0.010, 0.012, 0.015]
KS = [1.5, 2.0, 2.5, 3.0, 4.0]
lo, mid, hi = S.T0m, S.SPLITm, S.ENDm
out = {}
for age in (1e9, 10):
    M, SP = S.load(usdt_max_age=age, spreads=sp_author)
    const = lambda v: {s: np.full(M[s]["n"], v) for s in D.SYMS}
    for fm, op in {"same_close(author)": {}, "next_open": {"delay": 1, "delay_px": "open_ask"}, "next_close": {"delay": 1}}.items():
        fixed = {th: S.simulate(M, const(th), **op) for th in THRS}
        ktr = {k: S.simulate(M, {s: np.fmax(D.FLOOR, k * M[s]["vol"]) for s in D.SYMS}, **op) for k in KS}
        isum = lambda tr, s=None: (tr[(tr.t_open < mid) & ((tr.symbol == s) if s else True)]).ret_pct.sum()
        ch = {s: max(THRS, key=lambda th: (round(isum(fixed[th], s), 6), -th)) for s in D.SYMS}
        kg = max(KS, key=lambda k: (round(isum(ktr[k]), 6), -k))
        kpm = {s: max(KS, key=lambda k: (round(isum(ktr[k], s), 6), -k)) for s in D.SYMS}
        V = {"V4": fixed[0.007], "V1": S.simulate(M, {s: np.full(M[s]["n"], ch[s]) for s in D.SYMS}, **op), "V2": ktr[kg],
             "V2b": S.simulate(M, {s: np.fmax(D.FLOOR, kpm[s] * M[s]["vol"]) for s in D.SYMS}, **op),
             "V3": S.simulate(M, {s: np.full(M[s]["n"], D.FLOOR + SP[s] / 2) for s in D.SYMS}, **op)}
        key = f"{'all_hours' if age > 1e8 else 'daytime_usdt_fresh10'}|{fm}"
        res = {"choices": {"V1": {s: round(v * 100, 1) for s, v in ch.items()}, "k": kg, "kpm": kpm}}
        print(f"\n=== {key}  V1={res['choices']['V1']} k={kg}")
        for vn, tr in V.items():
            st_is, st_oos = S.stats(tr[tr.period == "IS"]), S.stats(tr[tr.period == "OOS"])
            d = S.boot_diff(tr, V["V4"], mid, hi) if vn != "V4" else None
            res[vn] = {"IS": st_is, "OOS": st_oos, "diff_vs_V4": d,
                       "OOS_by_market": {s: [int(((tr.period == "OOS") & (tr.symbol == s)).sum()), round(tr[(tr.period == "OOS") & (tr.symbol == s)].ret_pct.sum(), 3)] for s in D.SYMS}}
            print(f"  {vn:4s} IS n={st_is['n']:3d} sum={st_is['sum']:7.3f} | OOS n={st_oos['n']:3d} mean={st_oos['mean']} sum={st_oos['sum']:7.3f} win={st_oos['win']} exits={st_oos['exits']}"
                  + (f" | diff {d[0]:+.3f} ci90 {d[1]} p>0 {d[2]}" if d else ""))
        print("   global fixed thr OOS (n,sum):", {f"{th*100:.1f}": (int((fixed[th].period == 'OOS').sum()), round(fixed[th][fixed[th].period == 'OOS'].ret_pct.sum(), 2)) for th in THRS})
        print("   global fixed thr IS  (n,sum):", {f"{th*100:.1f}": (int((fixed[th].period == 'IS').sum()), round(fixed[th][fixed[th].period == 'IS'].ret_pct.sum(), 2)) for th in THRS})
        print("   V4 OOS by market:", res["V4"]["OOS_by_market"])
        print("   V1 OOS by market:", res["V1"]["OOS_by_market"])
        out[key] = res
(S.Path(__file__).resolve().parent / "out" / "a4_fillmodels.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
