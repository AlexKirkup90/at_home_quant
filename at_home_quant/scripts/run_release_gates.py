from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class GateResult:
    name: str
    ok: bool
    detail: str


def _run_command(name: str, command: list[str]) -> GateResult:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode == 0:
        return GateResult(name=name, ok=True, detail="ok")
    detail = completed.stdout + ("\n" if completed.stdout else "") + completed.stderr
    return GateResult(name=name, ok=False, detail=detail.strip())


def _security_scan(root: pathlib.Path) -> GateResult:
    patterns = [
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"(?i)(api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    ]
    findings: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in patterns:
            if pattern.search(text):
                findings.append(f"{path}: matched {pattern.pattern}")
                break
    if findings:
        return GateResult(name="security", ok=False, detail="\n".join(findings))
    return GateResult(name="security", ok=True, detail="ok")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run release gates for production promotion.")
    parser.add_argument("--quick", action="store_true", help="Run a reduced gate set.")
    args = parser.parse_args()

    gates: list[GateResult] = []
    if args.quick:
        gates.append(_run_command("unit", ["pytest", "-q"]))
        gates.append(_security_scan(pathlib.Path.cwd()))
    else:
        gates.append(
            _run_command(
                "unit+integration",
                ["pytest", "-q", "at_home_quant/tests/test_backend_pipeline.py", "at_home_quant/tests/test_advisor_service.py"],
            )
        )
        gates.append(
            _run_command(
                "data-contract",
                ["pytest", "-q", "at_home_quant/tests/test_data_health.py", "at_home_quant/tests/test_fetcher.py"],
            )
        )
        gates.append(
            _run_command(
                "backtest-regression",
                ["pytest", "-q", "at_home_quant/tests/test_backtest_service.py", "at_home_quant/tests/test_performance_calc.py"],
            )
        )
        gates.append(_run_command("full-test-suite", ["pytest", "-q"]))
        gates.append(_security_scan(pathlib.Path.cwd()))

    failed = [gate for gate in gates if not gate.ok]
    for gate in gates:
        status = "PASS" if gate.ok else "FAIL"
        print(f"[{status}] {gate.name}")
        if not gate.ok:
            print(gate.detail)
            print("-" * 60)
    if failed:
        sys.exit(1)
    print("All release gates passed.")


if __name__ == "__main__":
    main()
