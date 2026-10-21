"""Audit 1: reproduce, then stress the fill model and the walk-forward split; re-select variants under each model."""
import json
import numpy as np
import pandas as pd
import audit_sim as S
D = S.D

THRS = [0.007, 0.008, 0.009, 0.010, 0.012, 0.015]
KS = [1.5, 2.0, 2.5, 3.0, 4.0]
M, SP = S.load()

def const(v):
    return {s: np.full(M[s]["n"], v) for s in D.SYMS}

def win_sum(tr, lo, hi, sym=None):
    x = tr[(tr.t_open >= lo) & (tr.t_open < hi)]
    if sym:
        x = x[x.symbol == sym]
    return x.ret_pct.sum()

def run_scenario(name, opts, splits):
    out = {"scenario": name, "opts": opts}
    fixed = {th: S.simulate(M, const(th), **opts) for th in THRS}
    ktr = {k: S.simulate(M, {s: np.fmax(D.FLOOR, k * M[s]["vol"]) for s in D.SYMS}, **opts) for k in KS}
    v4 = fixed[0.007]
    v3 = S.simulate(M, {s: np.full(M[s]["n"], D.FLOOR + SP[s] / 2) for s in D.SYMS}, **opts)
    out["splits"] = {}
    for sname, (slo, shi, tlo, thi) in splits.items():
        ch = {s: max(THRS, key=lambda th: (round(win_sum(fixed[th], slo, shi, s), 6), -th)) for s in D.SYMS}
        kg = max(KS, key=lambda k: (round(win_sum(ktr[k], slo, shi), 6), -k))
        kpm = {s: max(KS, key=lambda k: (round(win_sum(ktr[k], slo, shi, s), 6), -k)) for s in D.SYMS}
        v1 = S.simulate(M, {s: np.full(M[s]["n"], ch[s]) for s in D.SYMS}, **opts)
        v2 = ktr[kg]
        v2b = S.simulate(M, {s: np.fmax(D.FLOOR, kpm[s] * M[s]["vol"]) for s in D.SYMS}, **opts)
        res = {}
        for vn, tr in (("V4", v4), ("V1", v1), ("V2", v2), ("V2b", v2b), ("V3", v3)):
            x = tr[(tr.t_open >= tlo) & (tr.t_open < thi)]
            sel = tr[(tr.t_open >= slo) & (tr.t_open < shi)]
            st = S.stats(x)
            st["select_sum"] = round(sel.ret_pct.sum(), 3); st["select_n"] = len(sel)
            if vn != "V4":
                st["diff_vs_V4"], st["ci90"], st["p_gt0"] = S.boot_diff(tr, v4, tlo, thi)
            res[vn] = st
        res["choices"] = {"V1": {s: v * 100 for s, v in ch.items()}, "V2_k": kg, "V2b_k": kpm}
        # single global threshold curve on the test window
        res["global_fixed_curve_test"] = {f"{th*100:.1f}": [int(((fixed[th].t_open >= tlo) & (fixed[th].t_open < thi)).sum()),
                                                            round(win_sum(fixed[th], tlo, thi), 3)] for th in THRS}
        out["splits"][sname] = res
    return out

day = 1440.0
T0m = S.T0m
SPLITS = {
    "author_4_3": (T0m, T0m + 4 * day, T0m + 4 * day, T0m + 7 * day),
    "sel_3_test_4": (T0m, T0m + 3 * day, T0m + 3 * day, T0m + 7 * day),
    "sel_5_test_2": (T0m, T0m + 5 * day, T0m + 5 * day, T0m + 7 * day),
    "reverse_sel_5to7_test_1to4": (T0m + 4 * day, T0m + 7 * day, T0m, T0m + 4 * day),
}
SCEN = {
    "author_model": {},
    "entry_next_minute": {"delay": 1},
    "entry_adverse_extra_half_spread": {"adverse": 1.0},
    "stops_on_candle_low": {"lowstop": True},
    "stops_on_low_fill_at_low": {"lowstop": True, "low_fill": "low"},
    "pessimistic_next_minute+low_stops": {"delay": 1, "lowstop": True},
}
ALL = {}
for sn, op in SCEN.items():
    r = run_scenario(sn, op, SPLITS)
    ALL[sn] = r
    print(f"\n=== {sn} {op}")
    for spn, res in r["splits"].items():
        print(f"  -- split {spn}: choices V1={res['choices']['V1']} k={res['choices']['V2_k']}")
        for vn in ("V4", "V1", "V2", "V2b", "V3"):
            st = res[vn]
            extra = f" diff {st['diff_vs_V4']:+.3f} ci90 {st['ci90']} p>0 {st['p_gt0']}" if vn != "V4" else ""
            print(f"    {vn:4s} test n={st['n']:3d} mean={st['mean']} sum={st['sum']:.3f} win={st['win']} exits={st['exits']} | sel n={st['select_n']} sum={st['select_sum']}{extra}")
        print("    global fixed curve on test (n, sum):", res["global_fixed_curve_test"])
S_OUT = S.Path(__file__).resolve().parent / "out"
S_OUT.mkdir(exist_ok=True)
(S_OUT / "a1_stress.json").write_text(json.dumps(ALL, indent=1, default=str), encoding="utf-8")
