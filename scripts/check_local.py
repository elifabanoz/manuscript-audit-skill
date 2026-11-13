#!/usr/bin/env python3
"""Run the repository validation gate without a hosted runner."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "skills" / "manuscript-audit" / "schemas"
PYTHON_TEST_DIRS = (
    "skills/manuscript-audit/tools",
    "bench/compliance-mutations",
    "bench/parsing",
    "bench/venue-rec",
    "bench/review-alignment",
    "bench/editor-decisions",
    "bench/stability",
)


def reject_non_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def main():
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.", file=sys.stderr)
        return 2
    if shutil.which("node") is None:
        print("Node.js is required on PATH.", file=sys.stderr)
        return 2
    if shutil.which("pandoc") is None:
        print("Pandoc is required for the full local gate.", file=sys.stderr)
        return 2
    missing = [name for name in ("pypdf", "scipy", "reportlab")
               if importlib.util.find_spec(name) is None]
    if missing:
        print(f"Missing Python packages: {', '.join(missing)}. Install the PDF and statistics requirements.",
              file=sys.stderr)
        return 2

    checks = [
        ("Generated agent definitions", [sys.executable,
                                         "skills/manuscript-audit/roles/gen_agents.py",
                                         "--check", "--output-root", "."]),
        ("Node validators", ["node", "--test",
                             "skills/manuscript-audit/validators/validators.test.cjs"]),
    ]
    checks.extend((directory, [sys.executable, "-m", "unittest", "discover",
                               "-s", directory, "-p", "test_*.py", "-q"])
                  for directory in PYTHON_TEST_DIRS)
    for label, command in checks:
        print(f"Checking {label}...", flush=True)
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        output = result.stdout + result.stderr
        if result.returncode:
            print(output, end="" if output.endswith("\n") else "\n", file=sys.stderr)
            print(f"Failed: {label}", file=sys.stderr)
            return result.returncode
        if label == "Node validators":
            print("\n".join(output.splitlines()[-9:]))
        elif output.strip():
            print(output.strip())

    print("Checking JSON schemas...", flush=True)
    try:
        files = sorted(SCHEMAS.iterdir())
        if not files or any(not path.is_file() for path in files):
            raise ValueError("Schema directory is empty or contains a non-file entry")
        for path in files:
            json.loads(path.read_text(encoding="utf-8"),
                       parse_constant=reject_non_json_constant)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Failed: JSON schemas: {error}", file=sys.stderr)
        return 1
    print(f"All local checks passed ({len(files)} schemas).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
