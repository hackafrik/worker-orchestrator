#!/usr/bin/env python3
"""Probe the environment and emit a JSON registry of available worker adapters.

Pure stdlib. No external dependencies.
Usage: python3 scripts/discover_workers.py > .worker-registry.json
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], timeout: int = 5) -> str | None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _which(name: str) -> str | None:
    path = shutil.which(name)
    return path if path else None


def _version_from_flag(binary: str, flag: str = "--version") -> str | None:
    out = _run([binary, flag])
    return out.splitlines()[0] if out else None


def _check_http(base_url: str, endpoint: str, timeout: int = 3) -> bool:
    try:
        # Pure stdlib HTTP probe
        import urllib.request
        req = urllib.request.Request(
            f"{base_url}{endpoint}",
            method="GET",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def discover() -> dict:
    adapters: list[dict] = []

    # --- CLI adapters ---
    cli_tools = [
        ("opencode-cli", "opencode", ["--version"]),
        ("codex-cli", "codex", ["--version"]),
        ("claude-code", "claude", ["--version"]),
        ("gemini-cli", "gemini", ["--version"]),
        ("continue-cli", "cn", ["--version"]),
        ("cline-cli", "cline", ["--version"]),
        ("cursor-agent", "agent", ["--version"]),
        ("aider", "aider", ["--version"]),
        ("goose", "goose", ["--version"]),
    ]

    for adapter_name, binary, version_flag in cli_tools:
        path = _which(binary)
        if path:
            version = _version_from_flag(binary, version_flag[0])
            adapters.append({
                "name": adapter_name,
                "category": "CLI",
                "available": True,
                "path": path,
                "version": version,
            })
        else:
            adapters.append({
                "name": adapter_name,
                "category": "CLI",
                "available": False,
                "path": None,
                "version": None,
            })

    # --- HTTP API adapters ---
    http_endpoints = [
        ("ollama-http", "http://localhost:11434", "/api/tags"),
        ("vllm-http", "http://localhost:8000", "/v1/models"),
    ]

    for adapter_name, base_url, endpoint in http_endpoints:
        reachable = _check_http(base_url, endpoint)
        adapters.append({
            "name": adapter_name,
            "category": "HTTP_API",
            "available": reachable,
            "base_url": base_url if reachable else None,
            "version": None,
        })

    # --- Docker ---
    docker_path = _which("docker")
    docker_version = None
    if docker_path:
        docker_version = _version_from_flag("docker", "--version")
    adapters.append({
        "name": "docker-runtime",
        "category": "DOCKER",
        "available": bool(docker_path),
        "path": docker_path,
        "version": docker_version,
    })

    # --- Python interpreter ---
    py_path = _which("python3") or _which("python")
    py_version = None
    if py_path:
        py_version = _version_from_flag(py_path, "--version")
    adapters.append({
        "name": "python3",
        "category": "PYTHON_SCRIPT",
        "available": bool(py_path),
        "path": py_path,
        "version": py_version,
    })

    return {
        "adapters": adapters,
        "generated_at": _run(["date", "-Iseconds"]) or "",
    }


def main() -> int:
    registry = discover()
    out_path = Path(".worker-registry.json")
    out_path.write_text(json.dumps(registry, indent=2) + "\n")
    print(json.dumps(registry, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
