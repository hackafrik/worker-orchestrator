#!/usr/bin/env python3
"""Multi-worker synthesis parity test between worker-orchestrator v1.4 and v2.

Compares:
- v1.4 direct helper synthesis across prepared worker output dirs
- v2 manifest-driven worker execution across multiple dummy workers, followed by synthesis

Focus: whether both approaches converge on the same merged output structure and
conflict handling for a realistic multi-worker code-output scenario.
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
WORK_DIR = Path(tempfile.mkdtemp(prefix="worker-orchestrator-multi-synth-"))

WORKER_A = """#!/usr/bin/env python3
from pathlib import Path
Path('main.py').write_text('# from worker a\\n')
print('DONE worker a')
"""

WORKER_B = """#!/usr/bin/env python3
from pathlib import Path
Path('main.py').write_text('# from worker b\\n')
Path('utils.py').write_text('# utils\\n')
print('DONE worker b')
"""

WORKER_C = """#!/usr/bin/env python3
from pathlib import Path
Path('README.md').write_text('# worker c\\n')
print('DONE worker c')
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
        normalized = normalized.replace("/v1_merged", "/<MERGED_DIR>")
        normalized = normalized.replace("/v2_merged", "/<MERGED_DIR>")
        normalized = normalized.replace("v1_merged", "<MERGED_DIR>")
        normalized = normalized.replace("v2_merged", "<MERGED_DIR>")
        normalized = normalized.replace("<WORK_DIR>/v1/", "<WORK_DIR>/<FLOW_DIR>/")
        normalized = normalized.replace("<WORK_DIR>/v2/", "<WORK_DIR>/<FLOW_DIR>/")
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
        {"main.py": "# from worker a\n"},
        {"main.py": "# from worker b\n", "utils.py": "# utils\n"},
        {"README.md": "# worker c\n"},
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
        ("worker_a.py", WORKER_A, "multi-worker-a", ["main.py"]),
        ("worker_b.py", WORKER_B, "multi-worker-b", ["main.py", "utils.py"]),
        ("worker_c.py", WORKER_C, "multi-worker-c", ["README.md"]),
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

    manifest_path, v2_worker_dirs = build_v2_manifest(v2_base)
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
            *(str(d) for d in v2_worker_dirs),
            "--output",
            str(v2_base / "v2_merged"),
            "--type",
            "code",
        ],
        cwd=v2_base,
    )
    v2_tree = _snapshot_tree(v2_base / "v2_merged")

    phase_results = v2_manifest["stdout"].get("phases", []) if isinstance(v2_manifest["stdout"], dict) else []
    v2_all_tasks_succeeded = all(
        result.get("final_status") == "SUCCEEDED"
        for phase in phase_results
        for result in phase.get("results", [])
    )

    synth_equal = _normalize(v1_synth) == _normalize(v2_synth)
    tree_equal = _normalize(v1_tree) == _normalize(v2_tree)

    summary = {
        "work_dir": str(WORK_DIR),
        "v1_repo": str(V1_DIR),
        "v2_repo": str(V2_DIR),
        "v1_synthesis": _normalize(v1_synth),
        "v2_manifest": _normalize(v2_manifest),
        "v2_synthesis": _normalize(v2_synth),
        "v1_tree": _normalize(v1_tree),
        "v2_tree": _normalize(v2_tree),
        "parity": {
            "v2_all_tasks_succeeded": v2_all_tasks_succeeded,
            "synthesis_result_match": synth_equal,
            "merged_tree_match": tree_equal,
        },
        "all_passed": bool(v2_all_tasks_succeeded and synth_equal and tree_equal),
    }

    report_path = V2_DIR / "eval" / "parity-multiworker-synthesis-report.json"
    report_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))

    if summary["all_passed"]:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
