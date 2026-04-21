#!/usr/bin/env python3
"""
Evaluate raw worker stdout/stderr logs (e.g. from subprocess.Popen capturing opencode/codex).
Detects API rate limits, hangs, boot success, and completion markers.

Usage:
    python3 evaluate_worker_logs.py --log /tmp/worker-a/output.log --provider opencode

Returns JSON:
    {
        "boot_success": true,
        "boot_details": "opencode v1.14.19 booted, 8 MCP servers loaded",
        "api_limit_hit": true,
        "api_limit_details": "OpenAI 429 usage_limit_reached, resets_in_seconds: 1300",
        "completion_detected": false,
        "hang_detected": true,
        "score": 1,
        "feedback": "Worker hung due to provider rate limit. Respawn on alternate provider or wait 1300s."
    }
"""

import argparse
import json
import re
import sys


def evaluate_boot(log_text):
    """Check if the worker process successfully booted."""
    if not log_text or not log_text.strip():
        return {"success": False, "details": "Log is empty — worker produced zero output (possible silent hang or immediate crash)"}

    # opencode boot markers
    version_match = re.search(r'opencode\s+v?([\d.]+)', log_text, re.I)
    mcp_match = re.search(r'mcp\s+key=(\w+).*connected', log_text, re.I)
    session_match = re.search(r'session\s+id=([a-zA-Z0-9_-]+)', log_text, re.I)

    details = []
    if version_match:
        details.append(f"opencode v{version_match.group(1)} booted")
    if mcp_match:
        details.append(f"MCP servers loaded")
    if session_match:
        details.append(f"Session created: {session_match.group(1)}")

    success = bool(version_match or session_match)
    return {"success": success, "details": "; ".join(details) or "No clear boot markers found"}


def evaluate_api_limits(log_text):
    """Detect provider rate limiting or quota exhaustion."""
    patterns = [
        (r'usage_limit_reached', "Provider quota exhausted"),
        (r'429', "HTTP 429 rate limit"),
        (r'rate.?limit', "Rate limit detected"),
        (r'too many requests', "Too many requests"),
        (r'resets_in_seconds[:\s]+(\d+)', "Quota resets in {0}s"),
        (r'resets_at[:\s]+(\d+)', "Quota resets at timestamp {0}"),
        (r'insufficient_quota', "Insufficient quota"),
    ]

    hits = []
    for pattern, desc_template in patterns:
        for match in re.finditer(pattern, log_text, re.I):
            desc = desc_template
            if match.groups():
                desc = desc_template.format(match.group(1))
            hits.append(desc)

    if hits:
        return {"hit": True, "details": "; ".join(hits)}
    return {"hit": False, "details": "No API limit indicators found"}


def evaluate_completion(log_text):
    """Look for signals that the worker actually finished its task."""
    completion_markers = [
        r'apply_patch\s+Success',
        r'Wrote to',
        r'Created\s+',
        r'DONE',
        r'Finished',
        r'Completed',
        r'Task complete',
        r'worker complete',
        r'Output written to',
    ]
    found = []
    for marker in completion_markers:
        if re.search(marker, log_text, re.I):
            found.append(marker)

    return {"detected": bool(found), "markers": found}


def evaluate_hang(log_text, boot_info, api_limit_info, completion_info):
    """Infer whether the worker hung rather than exited cleanly."""
    # If booted but no completion markers and API limit hit → hang
    if boot_info["success"] and not completion_info["detected"]:
        if api_limit_info["hit"]:
            return {"detected": True, "reason": "Booted successfully but blocked by API rate limit; process hung waiting for quota"}
        # Heuristic: long log with repetitive polling patterns
        repetitive = len(set(log_text.splitlines()[-20:])) < 5
        if repetitive:
            return {"detected": True, "reason": "Log shows repetitive output in final 20 lines — likely hung polling"}
        return {"detected": True, "reason": "Booted but no completion markers found; may have hung silently"}

    if not boot_info["success"] and not log_text.strip():
        return {"detected": True, "reason": "No output at all — immediate crash or hang before boot"}

    return {"detected": False, "reason": "Normal exit inferred"}


def score_and_feedback(boot_info, api_limit_info, completion_info, hang_info):
    score = 3  # neutral
    feedback_parts = []

    if not boot_info["success"]:
        score = 1
        feedback_parts.append("Worker failed to boot. Check installation and workdir setup.")
    elif api_limit_info["hit"]:
        score = 1
        feedback_parts.append(f"Provider API limit blocked execution: {api_limit_info['details']}")
        feedback_parts.append("Recommendation: respawn on alternate provider, use local model, or wait for quota reset.")
    elif hang_info["detected"]:
        score = 2
        feedback_parts.append(f"Worker hung: {hang_info['reason']}")
        feedback_parts.append("Recommendation: reduce timeout, check provider health before spawning batches.")
    elif not completion_info["detected"]:
        score = 2
        feedback_parts.append("Worker booted and exited but produced no recognizable completion markers. Output may be incomplete.")
    else:
        score = 5
        feedback_parts.append("Worker booted, executed, and produced completion markers.")

    return {"score": max(1, min(5, score)), "feedback": " ".join(feedback_parts)}


def main():
    parser = argparse.ArgumentParser(description="Evaluate worker process logs")
    parser.add_argument("--log", required=True, help="Path to worker stdout/stderr log file")
    parser.add_argument("--provider", default="unknown", help="Worker provider name (opencode, codex)")
    args = parser.parse_args()

    try:
        with open(args.log, "r", encoding="utf-8", errors="ignore") as f:
            log_text = f.read()
    except FileNotFoundError:
        print(json.dumps({
            "boot_success": False,
            "api_limit_hit": False,
            "completion_detected": False,
            "hang_detected": True,
            "score": 1,
            "feedback": f"Log file not found: {args.log}"
        }, indent=2))
        sys.exit(0)

    boot = evaluate_boot(log_text)
    api_limit = evaluate_api_limits(log_text)
    completion = evaluate_completion(log_text)
    hang = evaluate_hang(log_text, boot, api_limit, completion)
    scoring = score_and_feedback(boot, api_limit, completion, hang)

    result = {
        "provider": args.provider,
        "boot_success": boot["success"],
        "boot_details": boot["details"],
        "api_limit_hit": api_limit["hit"],
        "api_limit_details": api_limit["details"],
        "completion_detected": completion["detected"],
        "completion_markers": completion["markers"],
        "hang_detected": hang["detected"],
        "hang_reason": hang["reason"],
        "score": scoring["score"],
        "feedback": scoring["feedback"]
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
