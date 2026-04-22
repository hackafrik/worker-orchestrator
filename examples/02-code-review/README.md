# Example 02: Code Review

Four specialist reviewers analyze a Python module in parallel—bug hunting, style checking, performance analysis, and security auditing. Their findings merge into a single prioritized report.

## What it demonstrates

- **Specialist workers**: Each worker has a narrow domain and tailored rubric
- **Parallel execution**: All four reviewers run simultaneously
- **Complex rubrics**: Multiple weighted criteria per reviewer
- **Structured synthesis**: JSON output with severity-ranked findings
- **Real adapter usage**: Uses `python_script` adapter (not just `cli`)

## Run it

```bash
python3 ../../src/orchestrator.py manifest.json
```

## Expected behavior

1. Four Python scripts spawn in parallel
2. Each returns a structured review report
3. Synthesis worker merges all findings, deduplicates, prioritizes by severity
4. Final output: `code_review_report.json` with actionable items

## Scripts needed

- `bug_review.py` — Returns `{"findings": [{"line": 42, "severity": "high", "issue": "..."}]}`
- `style_review.py` — Returns PEP 8 violations and naming issues
- `perf_review.py` — Returns complexity and allocation findings
- `security_review.py` — Returns security vulnerabilities
- `synthesize_report.py` — Merges all inputs into final report
