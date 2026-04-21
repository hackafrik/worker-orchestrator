#!/usr/bin/env python3
"""
Evaluate a worker's output against criteria.

Usage:
    python3 evaluate_output.py --file <path> --criteria <json>
    python3 evaluate_output.py --file output.py --criteria '{"expected_keywords": ["def", "class"], "required_sections": ["imports", "main"]}'

Returns JSON:
    {
        "presence": {"passed": true, "reason": "File exists, 234 lines"},
        "correctness": {"score": 4, "reason": "Contains all expected keywords"},
        "completeness": {"score": 3, "reason": "Missing docstrings section"},
        "quality": {"score": 4, "reason": "No TODOs or placeholders found"},
        "average": 3.67,
        "passed": true,
        "feedback": "Add docstrings to all public functions."
    }
"""

import argparse
import json
import os
import re
import sys


def evaluate_presence(filepath):
    if not os.path.exists(filepath):
        return {"passed": False, "reason": f"File not found: {filepath}"}
    size = os.path.getsize(filepath)
    if size == 0:
        return {"passed": False, "reason": "File exists but is empty"}
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    non_empty = [l for l in lines if l.strip()]
    return {"passed": True, "reason": f"File exists, {len(non_empty)} non-empty lines, {size} bytes"}


def evaluate_correctness(filepath, criteria):
    score = 3  # neutral start
    reasons = []

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read().lower()

    # Check expected keywords
    expected = criteria.get("expected_keywords", [])
    missing = [kw for kw in expected if kw.lower() not in content]
    if missing:
        score -= 1
        reasons.append(f"Missing keywords: {', '.join(missing)}")
    elif expected:
        score += 1
        reasons.append("All expected keywords present")

    # Check forbidden patterns (indicators of bad output)
    forbidden = criteria.get("forbidden_patterns", ["TODO", "FIXME", "PLACEHOLDER", "XXX", "HACK"])
    found_forbidden = [p for p in forbidden if p.lower() in content]
    if found_forbidden:
        score -= 1
        reasons.append(f"Found forbidden patterns: {', '.join(found_forbidden)}")
    else:
        reasons.append("No forbidden patterns found")

    # File type specific checks
    if filepath.endswith(".py"):
        if "import" in content or "from " in content:
            pass  # has imports
        if "def " in content or "class " in content:
            pass  # has definitions

    score = max(1, min(5, score))
    return {"score": score, "reason": "; ".join(reasons) or "Basic correctness check passed"}


def evaluate_completeness(filepath, criteria):
    score = 3
    reasons = []

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read().lower()

    required = criteria.get("required_sections", [])
    missing = [s for s in required if s.lower() not in content]
    if missing:
        score -= 2
        reasons.append(f"Missing required sections: {', '.join(missing)}")
    elif required:
        score += 1
        reasons.append("All required sections present")

    # Check for substantial content
    word_count = len(content.split())
    if word_count < 50:
        score -= 1
        reasons.append(f"Very short output ({word_count} words)")
    elif word_count > 200:
        score += 1
        reasons.append(f"Substantial output ({word_count} words)")

    score = max(1, min(5, score))
    return {"score": score, "reason": "; ".join(reasons) or "Completeness check passed"}


def evaluate_quality(filepath, criteria):
    score = 3
    reasons = []

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # Check for common quality issues
    if "..." in content and content.count("...") > 5:
        score -= 1
        reasons.append("Excessive ellipsis suggests incomplete output")

    if re.search(r'\blorem ipsum\b', content, re.I):
        score -= 2
        reasons.append("Contains lorem ipsum placeholder text")

    # Check for balanced brackets/braces in code
    if filepath.endswith((".py", ".js", ".ts", ".json", ".java", ".c", ".cpp")):
        open_braces = content.count("{")
        close_braces = content.count("}")
        if open_braces != close_braces and open_braces > 0:
            score -= 1
            reasons.append(f"Unbalanced braces: {open_braces} open, {close_braces} close")
        else:
            reasons.append("Braces balanced")

    # Check line length (no extremely long lines)
    lines = content.split("\n")
    very_long = [i+1 for i, l in enumerate(lines) if len(l) > 300]
    if len(very_long) > 3:
        score -= 1
        reasons.append(f"{len(very_long)} lines exceed 300 chars")
    else:
        reasons.append("Line lengths reasonable")

    score = max(1, min(5, score))
    return {"score": score, "reason": "; ".join(reasons) or "Quality check passed"}


def main():
    parser = argparse.ArgumentParser(description="Evaluate worker output")
    parser.add_argument("--file", required=True, help="Path to worker output file")
    parser.add_argument("--criteria", default="{}", help="JSON criteria string")
    args = parser.parse_args()

    try:
        criteria = json.loads(args.criteria)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid criteria JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    presence = evaluate_presence(args.file)
    if not presence["passed"]:
        result = {
            "presence": presence,
            "correctness": {"score": 1, "reason": "N/A - no output"},
            "completeness": {"score": 1, "reason": "N/A - no output"},
            "quality": {"score": 1, "reason": "N/A - no output"},
            "average": 1.0,
            "passed": False,
            "feedback": f"Worker produced no output: {presence['reason']}"
        }
        print(json.dumps(result, indent=2))
        sys.exit(0)

    correctness = evaluate_correctness(args.file, criteria)
    completeness = evaluate_completeness(args.file, criteria)
    quality = evaluate_quality(args.file, criteria)

    average = round((correctness["score"] + completeness["score"] + quality["score"]) / 3, 2)
    passed = average >= 3.0

    # Generate feedback
    feedback_parts = []
    if correctness["score"] < 3:
        feedback_parts.append(f"Correctness issue: {correctness['reason']}")
    if completeness["score"] < 3:
        feedback_parts.append(f"Completeness issue: {completeness['reason']}")
    if quality["score"] < 3:
        feedback_parts.append(f"Quality issue: {quality['reason']}")

    if not feedback_parts:
        feedback = "Output meets all criteria. Good work."
    else:
        feedback = " ".join(feedback_parts)

    result = {
        "presence": presence,
        "correctness": correctness,
        "completeness": completeness,
        "quality": quality,
        "average": average,
        "passed": passed,
        "feedback": feedback
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
