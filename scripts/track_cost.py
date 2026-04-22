#!/usr/bin/env python3
"""Cost and token tracking ledger.

Aggregates per-worker spend. For local CLI/Python/Docker workers cost=0.
For API workers cost is derived from usage metadata or manual rates.
Pure stdlib. No external dependencies.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_RATES = {
    # OpenAI-ish rates (USD per 1M tokens)
    "openai:gpt-4.1": {"input": 2.00, "output": 8.00},
    "openai:gpt-4o": {"input": 2.50, "output": 10.00},
    "openai:gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "anthropic:claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "anthropic:claude-opus-4": {"input": 15.00, "output": 75.00},
    "anthropic:claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "deepseek:deepseek-chat": {"input": 0.27, "output": 1.10},
    "google:gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "groq:llama-4-scout": {"input": 0.13, "output": 0.34},
    "ollama:*": {"input": 0.0, "output": 0.0},
    "vllm:*": {"input": 0.0, "output": 0.0},
    "local:*": {"input": 0.0, "output": 0.0},
}


def _ledger_path() -> Path:
    return Path(os.environ.get("WORKER_LEDGER_PATH", ".worker-ledger.jsonl"))


def record(worker_id: str, category: str, adapter_name: str, model_id: str | None,
           tokens_in: int | None, tokens_out: int | None, runtime_seconds: float | None) -> dict:
    """Append a cost record to the JSONL ledger."""
    rate_key = model_id if model_id and model_id in DEFAULT_RATES else f"{adapter_name.split('-')[0]}:*" if adapter_name else "local:*"
    if rate_key not in DEFAULT_RATES:
        rate_key = "local:*"
    rates = DEFAULT_RATES[rate_key]

    in_tok = tokens_in or 0
    out_tok = tokens_out or 0
    cost = (in_tok / 1_000_000) * rates["input"] + (out_tok / 1_000_000) * rates["output"]

    record = {
        "worker_id": worker_id,
        "category": category,
        "adapter_name": adapter_name,
        "model_id": model_id,
        "tokens_in": in_tok,
        "tokens_out": out_tok,
        "cost_usd": round(cost, 6),
        "runtime_seconds": runtime_seconds,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    ledger = _ledger_path()
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def report(worker_ids: list[str] | None = None) -> dict[str, Any]:
    """Read ledger and return per-worker and total aggregates."""
    ledger = _ledger_path()
    if not ledger.exists():
        return {
            "total_cost_usd": 0.0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_runtime_seconds": 0.0,
            "by_worker": {},
            "by_category": {},
        }

    per_worker: dict[str, Any] = {}
    per_category: dict[str, Any] = {}
    total_cost = 0.0
    total_in = 0
    total_out = 0
    total_runtime = 0.0

    with open(ledger, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            wid = rec["worker_id"]
            cat = rec["category"]
            if worker_ids and wid not in worker_ids:
                continue
            for bucket, key in ((per_worker, wid), (per_category, cat)):
                if key not in bucket:
                    bucket[key] = {"cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "runtime_seconds": 0.0, "records": 0}
                bucket[key]["cost_usd"] += rec.get("cost_usd", 0)
                bucket[key]["tokens_in"] += rec.get("tokens_in", 0)
                bucket[key]["tokens_out"] += rec.get("tokens_out", 0)
                bucket[key]["runtime_seconds"] += rec.get("runtime_seconds", 0) or 0
                bucket[key]["records"] += 1
            total_cost += rec.get("cost_usd", 0)
            total_in += rec.get("tokens_in", 0)
            total_out += rec.get("tokens_out", 0)
            total_runtime += rec.get("runtime_seconds", 0) or 0

    return {
        "total_cost_usd": round(total_cost, 6),
        "total_tokens_in": total_in,
        "total_tokens_out": total_out,
        "total_runtime_seconds": round(total_runtime, 2),
        "by_worker": {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in per_worker.items()},
        "by_category": {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in per_category.items()},
    }


def clear() -> None:
    ledger = _ledger_path()
    if ledger.exists():
        ledger.unlink()


def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Worker cost ledger")
    sub = parser.add_subparsers(dest="cmd")

    rec = sub.add_parser("record")
    rec.add_argument("--worker-id", required=True)
    rec.add_argument("--category", default="unknown")
    rec.add_argument("--adapter", default="unknown")
    rec.add_argument("--model")
    rec.add_argument("--tokens-in", type=int)
    rec.add_argument("--tokens-out", type=int)
    rec.add_argument("--runtime", type=float)

    rep = sub.add_parser("report")
    rep.add_argument("--worker-ids", nargs="*")

    sub.add_parser("clear")

    args = parser.parse_args()
    if args.cmd == "record":
        out = record(
            args.worker_id, args.category, args.adapter, args.model,
            args.tokens_in, args.tokens_out, args.runtime,
        )
        print(json.dumps(out, indent=2))
        return 0
    elif args.cmd == "report":
        out = report(args.worker_ids)
        print(json.dumps(out, indent=2))
        return 0
    elif args.cmd == "clear":
        clear()
        print(json.dumps({"status": "cleared"}))
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
