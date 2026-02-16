#!/usr/bin/env python3
"""
Runner for the session-review agent defined in AGENT.md.

This script is the execution harness for the session-review agent.  The agent's
task specification (what it does, what it looks for, what it can edit) lives in
AGENT.md in this same directory.  This script handles the plumbing: validating
prerequisites, configuring tool permissions, launching the agent via the Claude
Code SDK, and streaming progress to stdout.

Usage:
    python agents/session-review/run.py [--model MODEL]

Requires:
    - claude-code-sdk  (pip install claude-code-sdk)
    - claude CLI authenticated and on PATH
"""

import argparse
import asyncio
import glob
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
#   agents/session-review/run.py  ->  project root is ../../
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_PROMPT = Path(__file__).resolve().parent / "AGENT.md"
SESSIONS_DIR = PROJECT_ROOT / "logs" / "sessions"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the session-review agent (see AGENT.md for task spec)"
    )
    parser.add_argument(
        "--model",
        default="sonnet",
        help="Claude model to use (default: sonnet)",
    )
    return parser.parse_args()


async def run_review(model: str):
    """Launch the session-review agent and stream progress to stdout."""

    if not AGENT_PROMPT.exists():
        print(f"Agent prompt not found: {AGENT_PROMPT}")
        return False

    session_files = glob.glob(str(SESSIONS_DIR / "*.json"))
    app_log = PROJECT_ROOT / "logs" / "app.log"
    if not session_files and not app_log.exists():
        print("No logs found in logs/. Run some traffic first.")
        return False

    print(f"Session logs: {len(session_files)} files")
    print(f"App log: {'found' if app_log.exists() else 'not found'}")
    print(f"Model: {model}")
    print("---")

    prompt = AGENT_PROMPT.read_text()

    # Tool permissions mirror what the agent spec in AGENT.md allows:
    #   - Read/Glob/Grep: full project access (read-only exploration)
    #   - Write: only to logs/reviews/ (report output)
    #   - Edit: only config/prompts/** (safe prompt fixes per AGENT.md Step 4)
    #   - Bash: only mkdir for the reviews directory
    #
    # permission_mode="acceptEdits" auto-approves these without a TTY,
    # which is what makes this work headlessly from make/cron/CI.
    options = ClaudeCodeOptions(
        model=model,
        cwd=str(PROJECT_ROOT),
        permission_mode="acceptEdits",
        allowed_tools=[
            "Read",
            "Glob",
            "Grep",
            "Write(logs/reviews/*)",
            "Edit(config/prompts/**)",
            "Bash(mkdir -p logs/reviews)",
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
    success = asyncio.run(run_review(args.model))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
