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
STATE_FILE = BOARDROOM_DIR / "state.json"

# Default state for the first run (no previous sessions reviewed)
DEFAULT_STATE = {
    "last_app_log_line": 0,
    "last_session_ts": None,
    "required_sessions": [],
}


def parse_args():
    parser = argparse.ArgumentParser(description="Run the Improvement Board cycle")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Claude model (default: sonnet)")
    parser.add_argument("--ceo-model", default="claude-opus-4-6", help="Model for Session CEO (default: opus)")
    return parser.parse_args()


def load_state() -> dict:
    """Load the incremental review state, or return defaults for the first run."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read {STATE_FILE}, starting fresh: {e}")
    return dict(DEFAULT_STATE)


def save_state(state: dict):
    """Persist the review state so the next run only processes new logs."""
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
    print(f"State saved: {STATE_FILE}")


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

    # --- Load incremental review state ---
    # The CEO agent uses this to skip already-reviewed sessions and log lines.
    state = load_state()
    print(f"Review state: last_session_ts={state['last_session_ts']}, "
          f"last_app_log_line={state['last_app_log_line']}, "
          f"required_sessions={len(state.get('required_sessions', []))} pending")

    # --- Step 1: Session CEO ---
    # The existing session-review runner reads AGENT.md as its prompt.
    # For boardroom mode, we call it with --boardroom which prepends the
    # BOARDROOM_MODE=true marker and redirects output to the boardroom dir.
    # --state passes the incremental review state so the agent only reads new logs.
    ceo_ok = run_agent(
        "Session CEO — analyzing logs",
        str(AGENTS_DIR / "session-review" / "run.py"),
        ["--boardroom", ceo_report, "--state", str(STATE_FILE)],
        args.ceo_model,
    )
    if not ceo_ok:
        write_lineage(record_id, ts, reports, "ceo_failed")
        print("\nCEO agent failed — cycle halted (state NOT updated).")
        sys.exit(1)

    # --- Update state after successful CEO run ---
    # The CEO agent writes updated state to state.json as its last action.
    # We reload it here to confirm it was written (and to log the new values).
    updated_state = load_state()
    print(f"State after CEO: last_session_ts={updated_state['last_session_ts']}, "
          f"last_app_log_line={updated_state['last_app_log_line']}")

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
