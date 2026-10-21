import json, glob
M = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot"
for d in sorted(glob.glob(M + "/runs/backtests/fv_sweep_*")):
    m = json.load(open(d + "/summary.json"))
    print("==", d.split("/")[-1], m["params"].get("ticks"), m["params"].get("entry_tmn"), m["params"].get("entry_usdt"))
    print(" spreads", m.get("spreads_from_live"))
    for s, v in m["markets"].items():
        if s.endswith("USDT"):
            print(" ", s, v)
