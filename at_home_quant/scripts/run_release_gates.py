from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass

from at_home_quant.config.settings import get_settings
from at_home_quant.db.session import get_session, init_db
from at_home_quant.ops.gates import record_release_gate_run
from at_home_quant.research.registry import code_hash


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
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run release gates for production promotion.")
    parser.add_argument("--quick", action="store_true", help="Run a reduced gate set.")
    parser.add_argument("--env", choices=["dev", "stage", "prod"], default=settings.app_env)
    parser.add_argument("--gate-name", default="release_gates")
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
    run_status = "failed" if failed else "passed"
    init_db()
    with get_session() as session:
        recorded = record_release_gate_run(
            session,
            environment=args.env,
            gate_name=args.gate_name,
            status=run_status,
            code_hash_value=code_hash(),
            details={
                "quick": args.quick,
                "results": [{"name": gate.name, "ok": gate.ok, "detail": gate.detail} for gate in gates],
            },
        )
        print(
            "gate artifact recorded: "
            + json.dumps(
                {
                    "id": recorded.id,
                    "environment": recorded.environment,
                    "gate_name": recorded.gate_name,
                    "status": recorded.status,
                    "code_hash": recorded.code_hash,
                }
            )
        )

    if failed:
        sys.exit(1)
    print("All release gates passed.")


if __name__ == "__main__":
    main()
