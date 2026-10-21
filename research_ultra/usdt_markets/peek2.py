import json, sys
M = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot/runs/"
for r in ["shadow_v2","shadow_v3","shadow_v4","shadow_v5","shadow_v6"]:
    for line in open(M + r + "/decisions.jsonl", encoding="utf-8"):
        try: e = json.loads(line)
        except Exception: continue
        if e.get("event") == "policy_review":
            p = e.get("policies", {})
            print(r, e["t"], e.get("risk_mode"), {s: (v["enabled"], v["entry_dev"], v["size_factor"]) for s, v in p.items() if s.endswith("USDT")})
# live
ev = {}
lines = open(M + "live_main/decisions.jsonl", encoding="utf-8").read().splitlines()
for line in lines:
    try: e = json.loads(line)
    except Exception: continue
    ev[e.get("event")] = ev.get(e.get("event"), 0) + 1
print(ev)
for line in lines[-400:]:
    try: e = json.loads(line)
    except Exception: continue
    if e.get("event") not in ("equity", "policy_review", "skip"):
        print(str(e)[:300])
    if e.get("event") == "equity":
        last = e
print(last)
