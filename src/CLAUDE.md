# src/ — Python source guardrails

These rules apply to all Python files in this directory. They're extracted from the project-level `CLAUDE.md` for visibility at the point where violations are most likely.

## No natural language in Python

Any text that a model reads belongs in a prompt file under `config/prompts/`, not in a Python string. This includes error guidance, behavioral instructions, truncation notes — anything that's a sentence rather than a variable name or log message.

The one exception: hardcoded fallback strings in `config.py`'s `load_prompt_file()` calls. These exist only so the service degrades gracefully if a prompt file is missing. The authoritative text is always the markdown file.

## Prompts are externalized

- Prompt templates: `config/prompts/`
- Env vars and constants: `config.py`
- Classification logic: `providers.py`
- Route orchestration: `app.py`
- Logging: `session_logger.py`

If you need to add model-facing text, create or edit a file under `config/prompts/` and load it via `config.py`. Python code does data processing and plumbing — it doesn't author prose.

## Separation of concerns

Each file has one job. If a change crosses a boundary, it belongs in a different file:
- `config.py` — loads env vars, loads prompt files, exposes constants
- `providers.py` — classification, enrichment, request forwarding
- `app.py` — Flask routes, pipeline dispatch
- `session_logger.py` — per-request JSON session logs
