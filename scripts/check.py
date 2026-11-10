"""Cross-platform checks; set GO/UV to portable executable paths if needed."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd=ROOT):
    subprocess.run(args, cwd=cwd, check=True)


if __name__ == "__main__":
    go, uv = os.environ.get("GO", "go"), os.environ.get("UV", "uv")
    run(
        [go, "test", "./...", "-coverprofile=coverage.out", "-covermode=atomic"],
        ROOT / "backend",
    )
    report = subprocess.check_output(
        [go, "tool", "cover", "-func=coverage.out"], cwd=ROOT / "backend", text=True
    )
    print(report)
    percent = float(report.strip().splitlines()[-1].split()[-1].rstrip("%"))
    if percent <= 80:
        sys.exit(f"Backend coverage must exceed 80%, got {percent}%")
    run(
        [
            uv,
            "run",
            "--project",
            "agent",
            "--frozen",
            "--extra",
            "dev",
            "pytest",
            "agent/tests",
            "--cov=buddy",
            "--cov-report=term-missing",
            "--cov-report=xml:reports/python-coverage.xml",
        ]
    )
