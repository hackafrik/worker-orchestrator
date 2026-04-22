#!/usr/bin/env python3
"""
Synthesize multiple worker outputs into a single coherent deliverable.

Usage:
    python3 synthesize_outputs.py --inputs <dir1> <dir2> <dir3> --output <final_dir> --type <code|report|docs>

--type code:     Merge code files, resolve conflicts, create unified project
--type report:   Combine text/markdown reports with section headers
--type docs:     Merge documentation files into single cohesive doc

Returns JSON:
    {
        "output_dir": "/path/to/final",
        "files_merged": ["file1.py", "file2.py", "README.md"],
        "conflicts": [],
        "notes": ["Merged 3 worker outputs into unified project"]
    }
"""

import argparse
import json
import os
import shutil
import sys


def merge_code_projects(input_dirs, output_dir):
    """Merge multiple code directories into one project."""
    notes = []
    conflicts = []
    files_merged = []

    os.makedirs(output_dir, exist_ok=True)

    for i, src_dir in enumerate(input_dirs):
        worker_label = f"worker_{chr(97 + i)}"  # worker_a, worker_b, ...

        for root, dirs, files in os.walk(src_dir):
            rel_root = os.path.relpath(root, src_dir)
            dest_root = os.path.join(output_dir, rel_root) if rel_root != "." else output_dir
            os.makedirs(dest_root, exist_ok=True)

            for fname in files:
                src = os.path.join(root, fname)
                dest = os.path.join(dest_root, fname)

                if os.path.exists(dest):
                    # Conflict: same file from multiple workers
                    conflict_name = f"{fname}.{worker_label}"
                    dest = os.path.join(dest_root, conflict_name)
                    conflicts.append(f"{rel_root}/{fname} -> renamed to {conflict_name}")

                shutil.copy2(src, dest)
                files_merged.append(os.path.relpath(dest, output_dir))

    notes.append(f"Merged {len(input_dirs)} code projects into {output_dir}")
    if conflicts:
        notes.append(f"Resolved {len(conflicts)} file conflicts by renaming")
    else:
        notes.append("No file conflicts detected")

    return {"files_merged": files_merged, "conflicts": conflicts, "notes": notes}


def merge_reports(input_dirs, output_dir):
    """Combine text/markdown reports into a single document."""
    notes = []
    sections = []

    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "combined_report.md")

    for i, src_dir in enumerate(input_dirs):
        worker_label = f"Worker {chr(65 + i)}"  # Worker A, Worker B, ...
        sections.append(f"\n{'=' * 60}\n# Section: {worker_label}\n{'=' * 60}\n")

        # Find markdown/text files
        report_files = []
        for root, dirs, files in os.walk(src_dir):
            for fname in files:
                if fname.endswith((".md", ".txt", ".rst")):
                    report_files.append(os.path.join(root, fname))

        if not report_files:
            sections.append(f"*No report files found in {src_dir}*\n")
            continue

        for rfile in sorted(report_files):
            with open(rfile, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            sections.append(f"\n## {os.path.basename(rfile)}\n\n{content}\n")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("# Combined Report\n\n")
        f.write("This document synthesizes outputs from multiple workers.\n")
        f.write("".join(sections))

    notes.append(f"Combined {len(input_dirs)} reports into {output_file}")

    return {
        "files_merged": ["combined_report.md"],
        "conflicts": [],
        "notes": notes
    }


def merge_docs(input_dirs, output_dir):
    """Merge documentation into a cohesive document."""
    notes = []

    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "combined_documentation.md")

    doc_parts = ["# Combined Documentation\n\n"]

    for i, src_dir in enumerate(input_dirs):
        worker_label = f"Worker {chr(65 + i)}"
        doc_parts.append(f"\n---\n\n## Contribution from {worker_label}\n\n")

        for root, dirs, files in os.walk(src_dir):
            for fname in sorted(files):
                if fname.endswith((".md", ".txt", ".rst")):
                    filepath = os.path.join(root, fname)
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    doc_parts.append(f"\n### {fname}\n\n{content}\n")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("".join(doc_parts))

    notes.append(f"Merged documentation from {len(input_dirs)} workers into {output_file}")

    return {
        "files_merged": ["combined_documentation.md"],
        "conflicts": [],
        "notes": notes
    }


def main():
    parser = argparse.ArgumentParser(description="Synthesize worker outputs")
    parser.add_argument("--inputs", nargs="+", required=True, help="Input directories from workers")
    parser.add_argument("--output", required=True, help="Output directory for synthesized result")
    parser.add_argument("--type", choices=["code", "report", "docs"], default="code",
                        help="Type of synthesis to perform")
    parser.add_argument("--phase", type=int, default=None,
                        help="Phase number for this synthesis (used for phase-based orchestration)")
    parser.add_argument("--dependency-inputs", nargs="*", default=[],
                        help="Previous phase output directories to include as context")
    args = parser.parse_args()

    # Validate inputs
    valid_inputs = []
    for d in args.inputs:
        if os.path.exists(d):
            valid_inputs.append(d)
        else:
            print(json.dumps({"error": f"Input directory not found: {d}"}), file=sys.stderr)
            sys.exit(1)

    if len(valid_inputs) < 1:
        print(json.dumps({"error": "At least one valid input directory required"}), file=sys.stderr)
        sys.exit(1)

    if args.type == "code":
        result = merge_code_projects(valid_inputs, args.output)
    elif args.type == "report":
        result = merge_reports(valid_inputs, args.output)
    elif args.type == "docs":
        result = merge_docs(valid_inputs, args.output)
    else:
        result = {"error": f"Unknown type: {args.type}"}

    result["output_dir"] = args.output
    result["type"] = args.type
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
