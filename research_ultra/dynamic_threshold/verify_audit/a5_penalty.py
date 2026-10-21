"""Audit 5: where does the ranking flip? Same-close fill plus a flat entry penalty p (0 / 0.1 / 0.2 / 0.3 %),
variants re-selected on days 1-4 under each p, tested on days 5-7 (author's spreads)."""
import json
import numpy as np
import audit_sim as S
D = S.D
R = json.loads((S.Path(__file__).resolve().parent.parent / "out" / "results_main.json").read_text(encoding="utf-8"))
sp = {s: v / 100 for s, v in R["params"]["spreads_pct"].items()}
M, SP = S.load(spreads=sp)
THRS = [0.007, 0.008, 0.009, 0.010, 0.012, 0.015]
KS = [1.5, 2.0, 2.5, 3.0, 4.0]
mid, hi = S.SPLITm, S.ENDm
const = lambda v: {s: np.full(M[s]["n"], v) for s in D.SYMS}
out = {}
for p in (0.0, 0.001, 0.002, 0.003):
    fixed = {th: S.simulate(M, const(th), flat=p) for th in THRS}
    ktr = {k: S.simulate(M, {s: np.fmax(D.FLOOR, k * M[s]["vol"]) for s in D.SYMS}, flat=p) for k in KS}
    isum = lambda tr, s=None: tr[(tr.t_open < mid) & ((tr.symbol == s) if s else True)].ret_pct.sum()
    ch = {s: max(THRS, key=lambda th: (round(isum(fixed[th], s), 6), -th)) for s in D.SYMS}
    kg = max(KS, key=lambda k: (round(isum(ktr[k]), 6), -k))
    V = {"V4": fixed[0.007], "V1": S.simulate(M, {s: np.full(M[s]["n"], ch[s]) for s in D.SYMS}, flat=p), "V2": ktr[kg],
         "V3": S.simulate(M, {s: np.full(M[s]["n"], D.FLOOR + SP[s] / 2) for s in D.SYMS}, flat=p)}
    print(f"\n=== flat entry penalty {p*100:.1f}%  V1={ {s: round(v*100,1) for s, v in ch.items()} } k={kg}")
    out[p] = {}
    for vn, tr in V.items():
        o = S.stats(tr[tr.period == "OOS"]); i = S.stats(tr[tr.period == "IS"])
        d = S.boot_diff(tr, V["V4"], mid, hi) if vn != "V4" else None
        out[p][vn] = {"IS": i, "OOS": o, "diff": d}
        print(f"  {vn:3s} IS n={i['n']:3d} sum={i['sum']:7.3f} | OOS n={o['n']:3d} mean={o['mean']} sum={o['sum']:7.3f} win={o['win']}" + (f" | diff {d[0]:+.3f} ci90 {d[1]} p>0 {d[2]}" if d else ""))
    print("   global fixed OOS (n,sum):", {f"{th*100:.1f}": (int((fixed[th].period == 'OOS').sum()), round(fixed[th][fixed[th].period == 'OOS'].ret_pct.sum(), 2)) for th in THRS})
(S.Path(__file__).resolve().parent / "out" / "a5_penalty.json").write_text(json.dumps({str(k): v for k, v in out.items()}, indent=1, default=str), encoding="utf-8")
