#!/usr/bin/env python3
"""
Runner for the doc-review agent defined in AGENT.md.

This script is the execution harness for the doc-review agent.  The agent's
task specification (what it does, what it checks, what it can edit) lives in
AGENT.md in this same directory.  This script handles the plumbing: validating
prerequisites, configuring tool permissions, launching the agent via the Claude
Code SDK, and streaming progress to stdout.

Usage:
    python agents/doc-review/run.py [--model MODEL]
    python agents/doc-review/run.py --boardroom <ceo_report> <challenger_report> [--model MODEL]

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
#   agents/doc-review/run.py  ->  project root is ../../
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_PROMPT = Path(__file__).resolve().parent / "AGENT.md"

# Key source files the agent needs to read — used as a sanity check
# that we're running from the right directory.
SOURCE_FILES = [
    PROJECT_ROOT / "src" / "config.py",
    PROJECT_ROOT / "src" / "providers.py",
    PROJECT_ROOT / "src" / "app.py",
    PROJECT_ROOT / "CLAUDE.md",
    PROJECT_ROOT / "README.md",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the doc-review agent (see AGENT.md for task spec)"
    )
    parser.add_argument(
        "--model",
        default="sonnet",
        help="Claude model to use (default: sonnet)",
    )
    parser.add_argument(
        "--boardroom",
        nargs=2,
        metavar=("CEO_REPORT", "CHALLENGER_REPORT"),
        help="Run in boardroom mode — validate proposals from these two reports",
    )
    return parser.parse_args()


async def run_review(model: str, boardroom_reports: list[str] | None = None):
    """Launch the doc-review agent and stream progress to stdout.

    Args:
        model: Claude model to use.
        boardroom_reports: If set, a [ceo_report, challenger_report] pair.
            The agent runs in QA Validator mode instead of full doc review.
    """

    if not AGENT_PROMPT.exists():
        print(f"Agent prompt not found: {AGENT_PROMPT}")
        return False

    if not boardroom_reports:
        # Standalone mode: verify source files exist
        missing = [f for f in SOURCE_FILES if not f.exists()]
        if missing:
            print("Missing source files (wrong working directory?):")
            for f in missing:
                print(f"  {f}")
            return False

    print(f"Project root: {PROJECT_ROOT}")
    print(f"Mode: {'boardroom (QA Validator)' if boardroom_reports else 'standalone'}")
    print(f"Model: {model}")
    print("---")

    prompt = AGENT_PROMPT.read_text()

    if boardroom_reports:
        ceo_report, challenger_report = boardroom_reports
        # Boardroom mode: prepend the marker so the agent acts as QA Validator
        # instead of doing a full doc review.
        prompt = (
            f"BOARDROOM_MODE=true\n\n"
            f"CEO report to validate: `{ceo_report}`\n"
            f"Challenger report to validate: `{challenger_report}`\n\n"
            f"---\n\n{prompt}"
        )

    # Tool permissions differ by mode:
    #   Standalone: can edit doc files (surgical fixes per AGENT.md Step 5)
    #   Boardroom: read-only + write report to boardroom dir only
    allowed_tools = [
        "Read",
        "Glob",
        "Grep",
        "Write(logs/reviews/*)",
        "Bash(mkdir -p logs/reviews)",
        "Bash(mkdir -p logs/reviews/boardroom)",
    ]
    if not boardroom_reports:
        # Standalone mode: allow doc edits
        allowed_tools.extend([
            "Edit(README.md)",
            "Edit(CLAUDE.md)",
            "Edit(docs/*)",
            "Edit(agents/session-review/AGENT.md)",
        ])

    # permission_mode="acceptEdits" auto-approves these without a TTY,
    # which is what makes this work headlessly from make/cron/CI.
    options = ClaudeCodeOptions(
        model=model,
        cwd=str(PROJECT_ROOT),
        permission_mode="acceptEdits",
        allowed_tools=allowed_tools,
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
    success = asyncio.run(run_review(args.model, args.boardroom))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        if "Event loop is closed" not in str(e):
            raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
