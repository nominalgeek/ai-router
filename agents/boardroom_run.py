#!/usr/bin/env python3
"""
Boardroom orchestrator — runs the three-role improvement cycle.

Executes the Improvement Board pipeline defined in review-board.yaml:
  1. Session CEO   — analyzes logs, writes proposals
  2. Challenger     — adversarially critiques each proposal
  3. QA Validator   — hard gate on surviving proposals

Each agent runs sequentially via the Claude Code SDK.  The CEO report path
is passed to the Challenger, and both report paths are passed to QA.  After
all three agents complete, a decision lineage record (JSON) is written to
logs/reviews/boardroom/.

Usage:
    python agents/boardroom_run.py [--model MODEL]

Preserves standalone agents:  make review  and  make doc-review  still work
independently — this orchestrator only governs the boardroom pipeline.
"""

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / "agents"
BOARDROOM_DIR = PROJECT_ROOT / "logs" / "reviews" / "boardroom"


def parse_args():
    parser = argparse.ArgumentParser(description="Run the Improvement Board cycle")
    parser.add_argument("--model", default="sonnet", help="Claude model (default: sonnet)")
    parser.add_argument("--ceo-model", default="opus", help="Model for Session CEO (default: opus)")
    return parser.parse_args()


def next_record_id() -> str:
    """Generate the next sequential BR-NNNN ID from existing records."""
    existing = sorted(BOARDROOM_DIR.glob("BR-*.json"))
    if not existing:
        return "BR-0001"
    last = existing[-1].stem  # e.g. "BR-0042"
    num = int(last.split("-")[1]) + 1
    return f"BR-{num:04d}"


def run_agent(label: str, runner: str, args: list[str], model: str) -> bool:
    """Run an agent runner script as a subprocess, streaming output."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}\n")
    cmd = [sys.executable, runner, "--model", model] + args
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    success = result.returncode == 0
    status = "completed" if success else "FAILED"
    print(f"\n  [{label}] {status}")
    return success


def write_lineage(record_id: str, timestamp: str, reports: dict, outcome: str):
    """Write an immutable decision lineage record (JSON)."""
    record = {
        "id": record_id,
        "timestamp": timestamp,
        "trigger": "manual",
        "reports": reports,
        "outcome": outcome,
    }
    path = BOARDROOM_DIR / f"{record_id}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    print(f"\nDecision lineage: {path}")


def main():
    args = parse_args()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    BOARDROOM_DIR.mkdir(parents=True, exist_ok=True)

    ceo_report = f"logs/reviews/boardroom/{ts}_ceo_report.md"
    challenger_report = f"logs/reviews/boardroom/{ts}_challenger_report.md"
    qa_report = f"logs/reviews/boardroom/{ts}_qa_report.md"

    reports = {"ceo": ceo_report, "challenger": challenger_report, "qa": qa_report}
    record_id = next_record_id()

    # --- Step 1: Session CEO ---
    # The existing session-review runner reads AGENT.md as its prompt.
    # For boardroom mode, we call it with --boardroom which prepends the
    # BOARDROOM_MODE=true marker and redirects output to the boardroom dir.
    ceo_ok = run_agent(
        "Session CEO — analyzing logs",
        str(AGENTS_DIR / "session-review" / "run.py"),
        ["--boardroom", ceo_report],
        args.ceo_model,
    )
    if not ceo_ok:
        write_lineage(record_id, ts, reports, "ceo_failed")
        print("\nCEO agent failed — cycle halted.")
        sys.exit(1)

    # --- Step 2: Adversarial Challenger ---
    challenger_ok = run_agent(
        "Adversarial Challenger — critiquing proposals",
        str(AGENTS_DIR / "challenger" / "run.py"),
        ["--ceo-report", ceo_report],
        args.model,
    )
    if not challenger_ok:
        write_lineage(record_id, ts, reports, "challenger_failed")
        print("\nChallenger agent failed — cycle halted.")
        sys.exit(1)

    # --- Step 3: QA Validator ---
    qa_ok = run_agent(
        "QA Validator — gating proposals",
        str(AGENTS_DIR / "doc-review" / "run.py"),
        ["--boardroom", ceo_report, challenger_report],
        args.model,
    )
    if not qa_ok:
        write_lineage(record_id, ts, reports, "qa_failed")
        print("\nQA agent failed — cycle halted.")
        sys.exit(1)

    # --- Decision lineage ---
    write_lineage(record_id, ts, reports, "completed")

    print(f"\nImprovement Board cycle {record_id} complete.")
    print(f"Review reports in: {BOARDROOM_DIR}/")


if __name__ == "__main__":
    main()
