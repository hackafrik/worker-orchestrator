#!/usr/bin/env python3
"""Real-world task parity test between worker-orchestrator v1.4 and v2.

Use case: generate a tiny multi-file Python CLI utility (a calculator with
add/sub/mul/div subcommands) via 3 workers, merge outputs, then smoke-test
the resulting merged project.

- v1.4: direct helper synthesis across prepared worker output dirs
- v2: manifest-driven worker execution + synthesis on artifact dirs

Both must produce a runnable merged project that passes a functional smoke test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

V1_DIR = Path("/home/hackafrik/worker-orchestrator")
V2_DIR = Path("/home/hackafrik/worker-orchestrator-v2")
WORK_DIR = Path(tempfile.mkdtemp(prefix="worker-orchestrator-realworld-"))

WORKER_MAIN = """#!/usr/bin/env python3
from pathlib import Path
Path('calculator.py').write_text('''#!/usr/bin/env python3
from cli import run
if __name__ == "__main__":
    run()
''')
print('DONE main')
"""

WORKER_CLI = """#!/usr/bin/env python3
from pathlib import Path
Path('cli.py').write_text('''#!/usr/bin/env python3
import sys
from operations import add, sub, mul, div

def run():
    if len(sys.argv) < 4:
        print("Usage: calculator.py <op> <a> <b>")
        sys.exit(1)
    op, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    if op == "add":
        print(add(a, b))
    elif op == "sub":
        print(sub(a, b))
    elif op == "mul":
        print(mul(a, b))
    elif op == "div":
        print(div(a, b))
    else:
        print("Unknown op")
        sys.exit(1)
''')
print('DONE cli')
"""

WORKER_OPS = """#!/usr/bin/env python3
from pathlib import Path
Path('operations.py').write_text('''#!/usr/bin/env python3
def add(a, b): return a + b
def sub(a, b): return a - b
def mul(a, b): return a * b
def div(a, b): return a / b if b != 0 else float('inf')
''')
print('DONE ops')
"""


def _run(cmd: list[str], cwd: Path, timeout: int = 180) -> dict:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    stdout = result.stdout.strip()
    parsed_stdout = None
    if stdout:
        try:
            parsed_stdout = json.loads(stdout)
        except json.JSONDecodeError:
            parsed_stdout = {"raw_stdout": stdout}
    else:
        parsed_stdout = {}
    return {"exit_code": result.returncode, "stdout": parsed_stdout, "stderr": result.stderr.strip()}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, str):
        normalized = value.replace(str(WORK_DIR), "<WORK_DIR>")
        normalized = normalized.replace("<WORK_DIR>/v1/", "<WORK_DIR>/<FLOW_DIR>/")
        normalized = normalized.replace("<WORK_DIR>/v2/", "<WORK_DIR>/<FLOW_DIR>/")
        normalized = normalized.replace("/v1_merged", "/<MERGED_DIR>")
        normalized = normalized.replace("/v2_merged", "/<MERGED_DIR>")
        normalized = normalized.replace("v1_merged", "<MERGED_DIR>")
        normalized = normalized.replace("v2_merged", "<MERGED_DIR>")
        # Normalize Python quote style differences between v1 (single) and v2 (double)
        normalized = normalized.replace("'", '"')
        return normalized
    return value


def _snapshot_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            out[rel] = path.read_text(errors="replace")
    return out


def prepare_v1_worker_dirs(base: Path) -> list[Path]:
    workers = []
    contents = [
        {"calculator.py": "#!/usr/bin/env python3\nfrom cli import run\nif __name__ == '__main__':\n    run()\n"},
        {"cli.py": "#!/usr/bin/env python3\nimport sys\nfrom operations import add, sub, mul, div\n\ndef run():\n    if len(sys.argv) < 4:\n        print('Usage: calculator.py <op> <a> <b>')\n        sys.exit(1)\n    op, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])\n    if op == 'add':\n        print(add(a, b))\n    elif op == 'sub':\n        print(sub(a, b))\n    elif op == 'mul':\n        print(mul(a, b))\n    elif op == 'div':\n        print(div(a, b))\n    else:\n        print('Unknown op')\n        sys.exit(1)\n"},
        {"operations.py": "#!/usr/bin/env python3\ndef add(a, b): return a + b\ndef sub(a, b): return a - b\ndef mul(a, b): return a * b\ndef div(a, b): return a / b if b != 0 else float('inf')\n"},
    ]
    for idx, mapping in enumerate(contents, start=1):
        d = base / f"worker_{idx}"
        d.mkdir(parents=True, exist_ok=True)
        for name, content in mapping.items():
            (d / name).write_text(content)
        workers.append(d)
    return workers


def build_v2_manifest(base: Path) -> tuple[Path, list[Path]]:
    scripts_dir = base / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    worker_specs = [
        ("worker_main.py", WORKER_MAIN, "realworld-main", ["calculator.py"]),
        ("worker_cli.py", WORKER_CLI, "realworld-cli", ["cli.py"]),
        ("worker_ops.py", WORKER_OPS, "realworld-ops", ["operations.py"]),
    ]

    artifact_dirs: list[Path] = []
    tasks = []
    for filename, script_content, task_id, expected_files in worker_specs:
        script_path = scripts_dir / filename
        _write(script_path, script_content)
        task_dir = base / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir = task_dir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_dirs.append(artifact_dir)
        tasks.append({
            "task_id": task_id,
            "worker_category": "PYTHON_SCRIPT",
            "adapter_name": "python3",
            "prompt": "",
            "workdir": str(task_dir),
            "artifacts_dir": str(artifact_dir),
            "timeout_seconds": 20,
            "poll_interval_seconds": 1,
            "pass_threshold": 1.0,
            "rubric": {
                "expected_output_markers": [],
                "expected_files": expected_files,
            },
            "runtime": {
                "script_path": str(script_path),
                "interpreter": sys.executable,
                "args": [],
                "cwd": str(artifact_dir),
            },
        })

    manifest = {"phases": [{"phase_number": 0, "tasks": tasks}]}
    manifest_path = base / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest_path, artifact_dirs


def smoke_test_project(project_dir: Path) -> dict:
    results: dict[str, dict] = {}
    for op, a, b, expected in [
        ("add", "2", "3", "5.0"),
        ("sub", "5", "3", "2.0"),
        ("mul", "4", "3", "12.0"),
        ("div", "6", "2", "3.0"),
    ]:
        result = subprocess.run(
            [sys.executable, "calculator.py", op, a, b],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        results[op] = {
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "expected": expected,
            "pass": result.returncode == 0 and result.stdout.strip() == expected,
        }
    return results


def main() -> int:
    v1_base = WORK_DIR / "v1"
    v2_base = WORK_DIR / "v2"
    v1_base.mkdir(parents=True, exist_ok=True)
    v2_base.mkdir(parents=True, exist_ok=True)

    v1_worker_dirs = prepare_v1_worker_dirs(v1_base / "workers")

    v1_synth = _run(
        [
            sys.executable,
            str(V1_DIR / "scripts/synthesize_outputs.py"),
            "--inputs",
            *(str(d) for d in v1_worker_dirs),
            "--output",
            str(v1_base / "v1_merged"),
            "--type",
            "code",
        ],
        cwd=v1_base,
    )
    v1_tree = _snapshot_tree(v1_base / "v1_merged")
    v1_smoke = smoke_test_project(v1_base / "v1_merged")

    manifest_path, v2_artifact_dirs = build_v2_manifest(v2_base)
    v2_manifest = _run(
        [sys.executable, str(V2_DIR / "scripts/run_orchestrator.py"), str(manifest_path)],
        cwd=v2_base,
        timeout=240,
    )
    v2_synth = _run(
        [
            sys.executable,
            str(V2_DIR / "scripts/synthesize_outputs.py"),
            "--inputs",
            *(str(d) for d in v2_artifact_dirs),
            "--output",
            str(v2_base / "v2_merged"),
            "--type",
            "code",
        ],
        cwd=v2_base,
    )
    v2_tree = _snapshot_tree(v2_base / "v2_merged")
    v2_smoke = smoke_test_project(v2_base / "v2_merged")

    phase_results = v2_manifest["stdout"].get("phases", []) if isinstance(v2_manifest["stdout"], dict) else []
    v2_all_tasks_succeeded = all(
        result.get("final_status") == "SUCCEEDED"
        for phase in phase_results
        for result in phase.get("results", [])
    )

    synth_equal = _normalize(v1_synth) == _normalize(v2_synth)
    tree_equal = _normalize(v1_tree) == _normalize(v2_tree)
    smoke_equal = _normalize(v1_smoke) == _normalize(v2_smoke)
    all_smoke_pass = all(r["pass"] for r in v1_smoke.values()) and all(r["pass"] for r in v2_smoke.values())

    summary = {
        "work_dir": str(WORK_DIR),
        "v1_repo": str(V1_DIR),
        "v2_repo": str(V2_DIR),
        "v1_synthesis": _normalize(v1_synth),
        "v2_manifest": _normalize(v2_manifest),
        "v2_synthesis": _normalize(v2_synth),
        "v1_tree": _normalize(v1_tree),
        "v2_tree": _normalize(v2_tree),
        "v1_smoke": _normalize(v1_smoke),
        "v2_smoke": _normalize(v2_smoke),
        "parity": {
            "v2_all_tasks_succeeded": v2_all_tasks_succeeded,
            "synthesis_result_match": synth_equal,
            "merged_tree_match": tree_equal,
            "smoke_test_match": smoke_equal,
            "all_smoke_passed": all_smoke_pass,
        },
        "all_passed": bool(v2_all_tasks_succeeded and synth_equal and tree_equal and smoke_equal and all_smoke_pass),
    }

    report_path = V2_DIR / "eval" / "parity-realworld-report.json"
    report_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))

    if summary["all_passed"]:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
