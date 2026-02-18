#!/usr/bin/env python3
"""
Runner for the adversarial challenger agent defined in AGENT.md.

This script is the execution harness for the challenger agent.  The agent's
task specification (what it evaluates, what verdicts it issues) lives in
AGENT.md in this same directory.  This script handles plumbing: locating the
CEO report, configuring tool permissions, launching the agent via the Claude
Code SDK, and streaming progress to stdout.

The challenger is strictly read-only for everything except its own report
in logs/reviews/boardroom/.  It reads the CEO report and session logs to
independently verify evidence, then writes a challenge report with per-
proposal verdicts.

Usage:
    python agents/challenger/run.py --ceo-report <path> [--model MODEL]

Requires:
    - claude-code-sdk  (pip install claude-code-sdk)
    - claude CLI authenticated and on PATH
"""

import argparse
import asyncio
import sys
from pathlib import Path

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

# Paths are relative to this file's location within the project tree:
#   agents/challenger/run.py  ->  project root is ../../
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_PROMPT = Path(__file__).resolve().parent / "AGENT.md"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the adversarial challenger agent (see AGENT.md for task spec)"
    )
    parser.add_argument(
        "--ceo-report",
        required=True,
        help="Path to the CEO report to challenge (e.g. logs/reviews/boardroom/2026-02-17_ceo_report.md)",
    )
    parser.add_argument(
        "--model",
        default="sonnet",
        help="Claude model to use (default: sonnet)",
    )
    return parser.parse_args()


async def run_challenge(ceo_report_path: str, model: str):
    """Launch the challenger agent and stream progress to stdout."""

    if not AGENT_PROMPT.exists():
        print(f"Agent prompt not found: {AGENT_PROMPT}")
        return False

    ceo_report = Path(ceo_report_path)
    if not ceo_report.exists():
        print(f"CEO report not found: {ceo_report}")
        return False

    sessions_dir = PROJECT_ROOT / "logs" / "sessions"
    if not sessions_dir.exists():
        print(f"Session logs directory not found: {sessions_dir}")
        return False

    print(f"CEO report: {ceo_report}")
    print(f"Session logs: {sessions_dir}")
    print(f"Model: {model}")
    print("---")

    # The agent prompt is the AGENT.md spec, plus the specific CEO report path
    # so the agent knows which report to evaluate.
    agent_spec = AGENT_PROMPT.read_text()
    prompt = (
        f"{agent_spec}\n\n"
        f"---\n\n"
        f"## This Run\n\n"
        f"The CEO report to evaluate is: `{ceo_report_path}`\n\n"
        f"Read it, then follow Steps 1-5 from your task spec above."
    )

    # Tool permissions: read-only for everything, write only to boardroom logs.
    # The challenger never edits prompt files or source code.
    options = ClaudeCodeOptions(
        model=model,
        cwd=str(PROJECT_ROOT),
        permission_mode="acceptEdits",
        allowed_tools=[
            "Read",
            "Glob",
            "Grep",
            "Write(logs/reviews/boardroom/*)",
            "Bash(mkdir -p logs/reviews/boardroom)",
        ],
    )

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
                elif isinstance(block, ToolUseBlock):
                    print(f"  -> {block.name}")

        elif isinstance(message, ResultMessage):
            print("\n---")
            if message.is_error:
                print(f"Agent failed after {message.num_turns} turns.")
            else:
                print(f"Done. {message.num_turns} turns, {message.duration_ms / 1000:.1f}s.")
            if message.total_cost_usd:
                print(f"Cost: ${message.total_cost_usd:.4f}")
            return not message.is_error

    return True


def main():
    args = parse_args()
    success = asyncio.run(run_challenge(args.ceo_report, args.model))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        main()
    except (SystemExit, KeyboardInterrupt):
        pass
    except RuntimeError as e:
        if "Event loop is closed" not in str(e):
            raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
