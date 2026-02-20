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
    python agents/session-review/run.py --boardroom <report_path> [--model MODEL]

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
    parser.add_argument(
        "--boardroom",
        metavar="REPORT_PATH",
        help="Run in boardroom mode — write proposals (not edits) to this report path",
    )
    parser.add_argument(
        "--state",
        metavar="STATE_FILE",
        help="Path to incremental review state JSON (boardroom mode only)",
    )
    return parser.parse_args()


async def run_review(model: str, boardroom_report: str | None = None, state_file: str | None = None):
    """Launch the session-review agent and stream progress to stdout.

    Args:
        model: Claude model to use.
        boardroom_report: If set, run in boardroom mode — the agent writes
            proposals to this path instead of applying edits directly.
        state_file: Path to incremental review state JSON.  When provided,
            the agent only processes logs newer than the stored watermarks.
    """

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
    print(f"Mode: {'boardroom' if boardroom_report else 'standalone'}")
    print(f"Model: {model}")
    print("---")

    prompt = AGENT_PROMPT.read_text()

    if boardroom_report:
        # Boardroom mode: prepend the marker so the agent writes structured
        # proposals instead of applying edits.  Also redirect report output
        # to the boardroom directory.
        state_block = ""
        if state_file:
            state_path = Path(state_file)
            if state_path.exists():
                state_json = state_path.read_text().strip()
                state_block = (
                    f"## Incremental Review State\n\n"
                    f"State file: `{state_file}`\n\n"
                    f"```json\n{state_json}\n```\n\n"
                    f"**Instructions:** Only analyze session logs with timestamps AFTER "
                    f"`last_session_ts` (or listed in `required_sessions`), and app.log "
                    f"lines after line `last_app_log_line`. If `last_session_ts` is null, "
                    f"this is the first run — analyze all available logs.\n\n"
                    f"**After analysis:** Update the state file by writing JSON to `{state_file}` with:\n"
                    f"- `last_session_ts`: ISO timestamp of the newest session you reviewed\n"
                    f"- `last_app_log_line`: the last line number of app.log you read\n"
                    f"- `required_sessions`: [] (clear after processing)\n\n"
                    f"---\n\n"
                )
            else:
                # First run — no state file yet, agent should review everything
                state_block = (
                    f"## Incremental Review State\n\n"
                    f"No previous state found (`{state_file}` does not exist). "
                    f"This is the first boardroom run — analyze all available logs.\n\n"
                    f"**After analysis:** Create the state file by writing JSON to `{state_file}` with:\n"
                    f"- `last_session_ts`: ISO timestamp of the newest session you reviewed\n"
                    f"- `last_app_log_line`: the last line number of app.log you read\n"
                    f"- `required_sessions`: []\n\n"
                    f"---\n\n"
                )
        prompt = (
            f"BOARDROOM_MODE=true\n\n"
            f"Write your report (with ## Proposals section) to: `{boardroom_report}`\n\n"
            f"{state_block}"
            f"---\n\n{prompt}"
        )

    # Tool permissions mirror what the agent spec in AGENT.md allows.
    # In boardroom mode, Edit is removed — proposals only, no direct edits.
    allowed_tools = [
        "Read",
        "Glob",
        "Grep",
        "Task",
        "Write(logs/reviews/*)",
        "Bash(mkdir -p logs/reviews)",
        "Bash(mkdir -p logs/reviews/boardroom)",
        "Bash(wc -l logs/app.log)",  # line count for incremental read
    ]
    if not boardroom_report:
        # Standalone mode: allow direct prompt edits (AGENT.md Step 4)
        allowed_tools.append("Edit(config/prompts/**)")

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
    success = asyncio.run(run_review(args.model, args.boardroom, args.state))
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
