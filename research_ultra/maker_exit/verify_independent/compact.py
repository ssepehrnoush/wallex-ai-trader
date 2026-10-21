import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
print("n", r["n_d1_4"], r["n_d5_7"], "spreads", r["spreads_pct"])
for k, v in r["arms"].items():
    print(f"{k:9s} n={v['n']} mean={v['mean']:+.3f} sum={v['sum']:+.2f} win={v['win']} hold={v['hold']} fill={v['maker_fill_pct']} {v['exits']} d1_4={v['d1_4']:+.3f} d5_7={v['d5_7']:+.3f}")
for k, v in r["paired"].items():
    print(f"{k:16s} {v['mean']:+.3f} t={v['t']:+.2f} ci={v['ci95']} drop3L={v['drop3_largest']:+.3f} bws={v['better_worse_same']} d1_4={v['d1_4']:+.3f} d5_7={v['d5_7']:+.3f}")
