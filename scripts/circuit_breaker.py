#!/usr/bin/env python3
"""
Circuit-breaker helper for worker-orchestrator.

Parses worker terminal output to detect rate-limit / API exhaustion errors.
Returns structured JSON for orchestrator decision-making.

Usage:
    python circuit_breaker.py <worker_output_file>
    python circuit_breaker.py -  # read from stdin
"""

import json
import re
import sys

RATE_LIMIT_PATTERNS = [
    # OpenAI / OpenCode patterns
    r"usage_limit_reached",
    r"rate_limit_reached",
    r"RateLimitError",
    r"rate limit exceeded",
    r"429",
    r"Too Many Requests",
    r"quota exceeded",
    r"insufficient_quota",
    r"resets_in_seconds",
    r"billing_hard_limit_reached",
    # Generic patterns
    r"API rate limit",
    r"retry-after",
]

PROVIDER_HINTS = {
    "openai": ["openai", "gpt-", "opencode", "o3", "o4"],
    "anthropic": ["anthropic", "claude", "opus", "sonnet"],
    "google": ["google", "gemini", "vertex"],
    "generic": [],
}


def detect_rate_limit(text: str) -> dict:
    """Scan worker output for rate-limit indicators."""
    text_lower = text.lower()
    matched_patterns = []

    for pattern in RATE_LIMIT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            matched_patterns.append(pattern)

    is_rate_limited = len(matched_patterns) > 0

    # Try to extract reset time
    reset_seconds = None
    reset_match = re.search(
        r"resets?[_\s]in[_\s](?:seconds?:?\s*)?(\d+)", text, re.IGNORECASE
    )
    if reset_match:
        reset_seconds = int(reset_match.group(1))
    else:
        # Fallback: look for "Retry-After: 123" or "retry after 123s"
        alt_match = re.search(
            r"retry[-_\s]?after[:\s]+(\d+)(?:s?)", text, re.IGNORECASE
        )
        if alt_match:
            reset_seconds = int(alt_match.group(1))

    # Try to identify provider
    provider = "unknown"
    text_for_provider = text_lower
    for prov, hints in PROVIDER_HINTS.items():
        for hint in hints:
            if hint in text_for_provider:
                provider = prov
                break
        if provider != "unknown":
            break

    # Determine severity
    severity = "none"
    if is_rate_limited:
        if reset_seconds and reset_seconds > 300:
            severity = "critical"  # >5min wait, respawn immediately on alt provider
        elif reset_seconds and reset_seconds > 60:
            severity = "high"  # >1min wait, respawn on alt provider
        else:
            severity = "medium"  # Short wait, could retry same provider after brief pause

    return {
        "is_rate_limited": is_rate_limited,
        "severity": severity,
        "provider": provider,
        "matched_patterns": matched_patterns,
        "reset_seconds": reset_seconds,
        "recommendation": _recommendation(is_rate_limited, severity, provider),
    }


def _recommendation(is_rate_limited: bool, severity: str, provider: str) -> str:
    if not is_rate_limited:
        return "none"
    if severity == "critical":
        return f"respawn_immediately_on_alternate_provider (current provider {provider} is exhausted)"
    if severity == "high":
        return f"respawn_on_alternate_provider (current provider {provider} rate-limited)"
    return "retry_same_provider_after_short_delay"


def main():
    if len(sys.argv) < 2:
        print("Usage: circuit_breaker.py <file> or circuit_breaker.py -", file=sys.stderr)
        sys.exit(1)

    source = sys.argv[1]
    if source == "-":
        text = sys.stdin.read()
    else:
        with open(source, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

    result = detect_rate_limit(text)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
