"""AI committee: two LLM roles decide on every rule-based candidate.

1. Market analyst: reads the multi-timeframe snapshot of the market (and BTC / USDT-TMN
   context) and writes an independent view: regime, setup quality, counter-signals.
2. Risk officer: reads the analyst's view plus the portfolio state and decides
   approve / reject, a size factor (0.25-1.0) and may TIGHTEN (never widen) stop/target.

Every request/response is cached on disk by a hash of its input, so backtests replay
exactly (reproducible) and the judges can re-run them without an API key.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from openai import OpenAI

ANALYST_SYSTEM = """You are the market analyst of an algorithmic trading desk on the Iranian crypto exchange Wallex.
Prices are in toman (TMN). Toman prices move with BOTH the coin's dollar price and the USDT/TMN rate.
You receive a rule-based LONG candidate and a compact multi-timeframe snapshot. Judge it on the data only:
trend alignment across 15m and 1h, momentum, volatility vs. the target, where price sits in its recent range,
volume, and the BTC and USDT/TMN context. Be skeptical: most candidates are mediocre.
Reply with JSON only:
{"regime": "uptrend|downtrend|range|volatile|unclear",
 "setup_quality": <integer 0-10>,
 "thesis": "<one or two sentences: why this could work>",
 "counter_signals": ["<what argues against it>", ...],
 "expected_move_pct": <your best estimate of the favourable move in % over the holding period>}"""

RISK_SYSTEM = """You are the risk officer of an algorithmic trading desk. Capital is small and the competition ranks
NET return in toman after fees (maker 0.25%, taker 0.30%), but also punishes drawdown. A losing book scores zero,
so capital preservation beats activity. You may approve, shrink, or reject. You may tighten the stop or lower the
target if the analyst's view justifies it, never widen the stop beyond the proposed one.
Reply with JSON only:
{"decision": "approve|reject",
 "confidence": <0.0-1.0>,
 "size_factor": <0.25-1.0, how much of the standard risk budget to use; ignored when rejecting>,
 "stop_pct": <stop distance as a fraction of entry, <= proposed>,
 "tp_pct": <target distance as a fraction of entry>,
 "reason_fa": "<one short sentence in Persian explaining the decision for the decision log>"}"""


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


class AICommittee:
    def __init__(self, cfg: dict, secrets, cache_dir: Path, offline: bool = False):
        self.cfg = cfg
        self.offline = offline
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        provider = cfg.get("provider", "openai")
        clients = {
            "openai": (OpenAI(api_key=secrets.openai_key) if secrets.openai_key else None, cfg.get("model", "gpt-5.4-mini")),
            "boxapi": (OpenAI(base_url="https://ai.boxapi.ir/ai/v1", api_key=secrets.boxapi_key) if secrets.boxapi_key else None,
                       cfg.get("boxapi_model", "openai/gpt-5.4-mini")),
        }
        self.client, self.model = clients[provider]
        # the other provider is a fallback: OpenAI needs a VPN from Iran, BoxAPI is an Iranian gateway
        other = "boxapi" if provider == "openai" else "openai"
        self.fallback = clients[other] if cfg.get("fallback", True) and clients[other][0] else None
        self.provider = provider
        self.calls = 0
        self.cache_hits = 0

    # -- LLM plumbing -----------------------------------------------------
    def _ask(self, role: str, system: str, payload: dict) -> dict:
        key = _hash({"role": role, "model": self.model, "system": system, "payload": payload})
        path = self.cache_dir / f"{role}_{key}.json"
        if path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        if self.offline or self.client is None:
            raise RuntimeError("AI decision not cached and no live model available")
        kwargs = dict(model=self.model, response_format={"type": "json_object"},
                      messages=[{"role": "system", "content": system},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
        if self.cfg.get("reasoning_effort"):
            kwargs["reasoning_effort"] = self.cfg["reasoning_effort"]
        t0 = time.time()
        last = None
        attempts = [(self.client, self.model)] * 2 + ([self.fallback] * 2 if self.fallback else [])
        used = self.model
        for n, (client, model) in enumerate(attempts):
            try:
                r = client.chat.completions.create(timeout=self.cfg.get("timeout_s", 45), **{**kwargs, "model": model})
                resp = json.loads(r.choices[0].message.content)
                used = model
                break
            except Exception as e:  # network, geo-block, JSON or rate limit: retry, then fall back, then give up
                last = e
                time.sleep(1.5 * (n + 1))
        else:
            raise RuntimeError(f"LLM failed: {last}")
        self.calls += 1
        path.write_text(json.dumps({"role": role, "model": used, "latency_s": round(time.time() - t0, 2),
                                    "payload": payload, "response": resp}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        return resp

    # -- decision ---------------------------------------------------------
    def decide(self, cand, snapshot: dict) -> dict:
        """Returns a dict with analyst, officer and a normalized verdict. Never raises:
        any failure becomes a logged rejection (no AI, no trade).
        The committee judges trade merit only; portfolio limits belong to risk.py. Keeping the
        input free of portfolio state makes live and backtest decisions identical for identical bars."""
        payload = {"candidate": {k: v for k, v in cand.to_dict().items() if k not in ("features",)},
                   "snapshot": snapshot}
        try:
            analyst = self._ask("analyst", ANALYST_SYSTEM, payload)
            officer = self._ask("officer", RISK_SYSTEM, {"candidate": payload["candidate"], "analyst": analyst})
        except Exception as e:
            return {"approved": False, "size_factor": 0.0, "error": str(e)[:300],
                    "reason_fa": "هوش مصنوعی در دسترس نبود؛ طبق قانون بدون تأیید AI معامله نمی‌شود."}

        approved = str(officer.get("decision", "")).lower() == "approve"
        conf = float(officer.get("confidence", 0) or 0)
        if conf < self.cfg.get("min_confidence", 0.55):
            approved = False
        q = int(analyst.get("setup_quality", 0) or 0)
        if q < self.cfg.get("min_setup_quality", 5):
            approved = False
        size = min(max(float(officer.get("size_factor", 0.5) or 0.5), 0.25), 1.0) if approved else 0.0
        stop_pct = min(float(officer.get("stop_pct", cand.stop_pct) or cand.stop_pct), cand.stop_pct)
        stop_pct = max(stop_pct, self.cfg.get("stop_floor_pct", 0.006))
        tp_pct = float(officer.get("tp_pct", cand.tp_pct) or cand.tp_pct)
        tp_pct = min(max(tp_pct, self.cfg.get("tp_floor_pct", 0.009)), cand.tp_pct * 1.25)
        return {"approved": approved, "size_factor": size, "confidence": conf, "setup_quality": q,
                "stop_pct": stop_pct, "tp_pct": tp_pct, "reason_fa": officer.get("reason_fa", ""),
                "analyst": analyst, "officer": officer}


def snapshot(f15, i: int, ctx: dict) -> dict:
    """Compact, model-readable view of the market at bar i (closed bars only)."""
    w = f15.iloc[max(0, i - 23): i + 1]
    last = f15.iloc[i]
    bars = [[round(r.open, 2), round(r.high, 2), round(r.low, 2), round(r.close, 2), round(r.volume, 4)]
            for r in w.itertuples()]
    def r6(x):
        try:
            return round(float(x), 6)
        except (TypeError, ValueError):
            return None
    return {
        "last_24_bars_15m_ohlcv": bars,
        "indicators_15m": {k: r6(last.get(k)) for k in ("ema20", "ema50", "rsi", "atr_pct", "bb_pos", "vol_z", "ret_1h", "ret_4h")},
        "indicators_1h": {k: r6(last.get(k)) for k in ("h_ema20", "h_ema50", "h_rsi", "h_adx", "h_atr_pct", "h_slope")},
        "context": ctx,
    }
