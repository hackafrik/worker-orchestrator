#!/usr/bin/env python3
"""Formal state machine for worker lifecycle management.

Pure stdlib. Thread-safe via file locking (fcntl on Unix, atomic rename fallback).
States and transitions are validated. Events are persisted to JSONL for audit.
"""

import fcntl
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Valid states
STATES = {
    "PENDING",
    "SPAWNING",
    "RUNNING",
    "MONITORING",
    "EVALUATING",
    "SUCCEEDED",
    "FAILED",
    "KILLED",
    "TIMED_OUT",
    "CANCEL_REQUESTED",
}

# Valid transitions: from_state -> set(to_states)
TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"SPAWNING", "CANCEL_REQUESTED"},
    "SPAWNING": {"RUNNING", "FAILED", "TIMED_OUT", "CANCEL_REQUESTED"},
    "RUNNING": {"MONITORING", "FAILED", "KILLED", "TIMED_OUT", "CANCEL_REQUESTED"},
    "MONITORING": {"EVALUATING", "FAILED", "KILLED", "TIMED_OUT", "CANCEL_REQUESTED"},
    "EVALUATING": {"SUCCEEDED", "FAILED", "KILLED", "TIMED_OUT"},
    "SUCCEEDED": set(),
    "FAILED": set(),
    "KILLED": set(),
    "TIMED_OUT": set(),
    "CANCEL_REQUESTED": {"KILLED", "TIMED_OUT", "FAILED"},
}

# Terminal states
TERMINAL = {"SUCCEEDED", "FAILED", "KILLED", "TIMED_OUT"}


class StateMachine:
    def __init__(self, state_dir: str | Path = ".worker-state"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _worker_file(self, worker_id: str) -> Path:
        return self.state_dir / f"{worker_id}.json"

    def _events_file(self) -> Path:
        return self.state_dir / "events.jsonl"

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _lock_file(self, f) -> None:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except (ImportError, AttributeError):
            pass  # Windows or no fcntl

    def _unlock_file(self, f) -> None:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (ImportError, AttributeError):
            pass

    def get_state(self, worker_id: str) -> dict | None:
        path = self._worker_file(worker_id)
        if not path.exists():
            return None
        with open(path, "r") as f:
            self._lock_file(f)
            try:
                data = json.load(f)
            finally:
                self._unlock_file(f)
            return data

    def transition(self, worker_id: str, new_status: str, reason: str = "") -> dict:
        if new_status not in STATES:
            raise ValueError(f"Invalid state: {new_status}. Valid: {STATES}")

        path = self._worker_file(worker_id)
        with self._lock:
            # Load or init
            if path.exists():
                with open(path, "r") as f:
                    self._lock_file(f)
                    try:
                        state = json.load(f)
                    finally:
                        self._unlock_file(f)
            else:
                state = {
                    "worker_id": worker_id,
                    "status": "PENDING",
                    "history": [],
                    "created_at": self._now(),
                    "updated_at": self._now(),
                }

            current = state["status"]
            if current == new_status:
                return state

            allowed = TRANSITIONS.get(current, set())
            if new_status not in allowed and current not in TERMINAL:
                raise ValueError(
                    f"Invalid transition: {current} -> {new_status}. Allowed: {allowed}"
                )
            if current in TERMINAL:
                raise ValueError(f"Cannot transition from terminal state {current}")

            # Apply transition
            state["status"] = new_status
            state["updated_at"] = self._now()
            state["history"].append({
                "from": current,
                "to": new_status,
                "at": self._now(),
                "reason": reason,
            })

            # Write atomically
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
                f.write("\n")
            os.replace(str(tmp), str(path))

            # Append event to JSONL
            events_path = self._events_file()
            with open(events_path, "a") as f:
                f.write(json.dumps({
                    "worker_id": worker_id,
                    "from": current,
                    "to": new_status,
                    "at": self._now(),
                    "reason": reason,
                }, ensure_ascii=False) + "\n")

            return state

    def list_by_status(self, status: str | None = None) -> list[dict]:
        results: list[dict] = []
        for f in self.state_dir.glob("*.json"):
            if f.name == "events.jsonl":
                continue
            state = self.get_state(f.stem)
            if state is None:
                continue
            if status is None or state["status"] == status:
                results.append(state)
        return results

    def is_terminal(self, worker_id: str) -> bool:
        state = self.get_state(worker_id)
        if state is None:
            return False
        return state["status"] in TERMINAL

    def can_transition(self, worker_id: str, new_status: str) -> bool:
        state = self.get_state(worker_id)
        if state is None:
            return new_status == "PENDING"
        current = state["status"]
        if current in TERMINAL:
            return False
        return new_status in TRANSITIONS.get(current, set())

    def history(self, worker_id: str) -> list[dict]:
        state = self.get_state(worker_id)
        if state is None:
            return []
        return state.get("history", [])


def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Worker state machine")
    sub = parser.add_subparsers(dest="cmd")

    init_p = sub.add_parser("init")
    init_p.add_argument("worker_id")

    trans_p = sub.add_parser("transition")
    trans_p.add_argument("worker_id")
    trans_p.add_argument("new_status")
    trans_p.add_argument("--reason", default="")

    list_p = sub.add_parser("list")
    list_p.add_argument("--status", default=None)

    get_p = sub.add_parser("get")
    get_p.add_argument("worker_id")

    hist_p = sub.add_parser("history")
    hist_p.add_argument("worker_id")

    args = parser.parse_args()

    sm = StateMachine()

    if args.cmd == "init":
        state = sm.transition(args.worker_id, "PENDING", reason="initialized")
        print(json.dumps(state, indent=2))
        return 0
    elif args.cmd == "transition":
        try:
            state = sm.transition(args.worker_id, args.new_status, reason=args.reason)
            print(json.dumps(state, indent=2))
            return 0
        except ValueError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            return 1
    elif args.cmd == "list":
        states = sm.list_by_status(args.status)
        print(json.dumps(states, indent=2))
        return 0
    elif args.cmd == "get":
        state = sm.get_state(args.worker_id)
        if state is None:
            print(json.dumps({"error": "not found"}), file=sys.stderr)
            return 1
        print(json.dumps(state, indent=2))
        return 0
    elif args.cmd == "history":
        h = sm.history(args.worker_id)
        print(json.dumps(h, indent=2))
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(_cli())
