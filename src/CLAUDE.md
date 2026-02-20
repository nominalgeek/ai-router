# src/ — Python source guardrails

These rules apply to all Python files in this directory. They're extracted from the project-level `CLAUDE.md` for visibility at the point where violations are most likely.

## No natural language in Python

Any text that a model reads belongs in a prompt file under `config/prompts/`, not in a Python string. This includes error guidance, behavioral instructions, truncation notes — anything that's a sentence rather than a variable name or log message.

The one exception: hardcoded fallback strings in `config.py`'s `load_prompt_file()` calls. These exist only so the service degrades gracefully if a prompt file is missing. The authoritative text is always the markdown file. **Every fallback activation must emit a `logger.error`** — silent degradation violates the No-Facades Rule (Operator Profile §3.1). A service running on fallback prompts is running in a degraded state; the logs must make that obvious.

If you need to add model-facing text, create or edit a file under `config/prompts/` and load it via the **Configuration** boundary. Python code does data processing and plumbing — it doesn't author prose.

## vLLM containers are not directly accessible

The vLLM containers (`router` and `primary`) run on an isolated Docker network (`ai-internal`) with no Traefik routes and no exposed ports. The **only** way to reach them is through the Flask app (`ai-router`), which bridges `ai-network` and `ai-internal`. Never add Traefik labels, port mappings, or external network memberships to the vLLM services — all model access must go through the Flask app so that classification, logging, and rate limiting cannot be bypassed.

## Separation of concerns

Four boundaries define the source layout. Each boundary has one responsibility. If a change crosses a boundary, it belongs in a different file.

| Boundary | Current file(s) | Responsibility |
|----------|-----------------|----------------|
| **Configuration** | `config.py` | Env vars, prompt loading, constants |
| **Providers** | `providers.py` | Classification, enrichment, request forwarding |
| **Routing** | `app.py` | Flask routes, pipeline dispatch |
| **Logging** | `session_logger.py` | Per-request JSON session logs |

As complexity grows, a boundary may be split into multiple files — but the boundary itself doesn't change. For example, `providers.py` could split into `classify.py`, `enrich.py`, and `forward.py` if it gets unwieldy, but all three still belong to the **Providers** boundary. The rule is: never let a split introduce cross-boundary coupling. A new file must fit cleanly inside exactly one boundary. If it doesn't, the boundary definition is wrong — fix that first.

**Boundary contracts** (what each boundary guarantees — per Operator Profile §3.3, §5.1):

- **Configuration** — every env var and prompt file used by the system is declared here and nowhere else. Other boundaries import from Configuration; they never read env vars or load files directly.
- **Providers** — given a route name, messages, and parameters, produces a response or a structured error. Classification always returns a valid route name (defaulting to `primary` on failure, with a logged warning). Forwarding never silently swallows errors — a failed API call returns an error response, never an empty success.
- **Routing** — every inbound request gets a session logger, gets classified (or fast-pathed for meta), and gets dispatched to exactly one pipeline. No request exits the Routing boundary without a session JSON being saved.
- **Logging** — every session JSON written to disk contains the fields documented in `agents/session-review/AGENT.md`. The schema is the contract with the Improvement Board; changing it requires updating the agent specs.

## Observability

Every request must be traceable end-to-end. This project has no metrics dashboards or APM — **session logs and structured application logs are the only observability layer.** If something goes wrong, those files are the only way to reconstruct what happened.

Observability also serves a second purpose: **session logs are the input to the Improvement Board** (see `agents/` and `review-board.yaml`). The autonomous review cycle — Session CEO, Challenger, QA Validator — consumes session JSONs to identify misclassifications, latency outliers, enrichment failures, and prompt weaknesses. The quality of that feedback loop is bounded by the quality of what we log. If a decision isn't captured in the session JSON, the boardroom can't evaluate it and can't propose improvements. Logging isn't just for debugging — it's the data layer that makes the pipeline self-improving.

**What every code path must provide:**

- **Session JSON** — one file per request in `logs/sessions/`, capturing the full lifecycle: route decision, every API call (with timing, status, and truncated response), and any errors. This is the primary debugging artifact and the input for the autonomous review agent.
- **Structured app log lines** — `logger.info` / `logger.warning` / `logger.error` in `logs/app.log`. These are the real-time signal during operation. Every significant decision point (classification result, route taken, fallback triggered, timeout hit) should emit a log line with enough context to understand *what happened* and *why* without cross-referencing another file.
- **Timing** — every external call (classification, enrichment, forwarding) must record `duration_ms`. Latency is the first thing to check when something feels wrong. Don't log timing for trivial internal operations.

**Balance signal with noise:**

Not every code change needs new logging. The test is: *if this code path fails or behaves unexpectedly at 2 AM, will the logs tell me what happened without adding a breakpoint?* If yes, the logging is sufficient. If no, add the minimum needed to close that gap. Specifically:

- Log **decisions** (route chosen, fallback triggered, timeout exceeded), not routine success.
- Log **boundaries** (entering/exiting a pipeline stage, external API call/response), not internal computation.
- Include **context** in each line (session ID, route, provider, duration) so lines are useful in isolation — don't rely on reading surrounding lines for context.
- **Errors always get logged** — with enough detail to identify the cause without reproduction. Include the failing URL, status code, and truncated error message.
- Don't log request/response bodies at INFO level — they go in session JSONs. App logs are for summaries and signals, not payloads.

**When changing code, validate observability:**

After modifying any request-handling code path, ask: can I trace a request through this change using only `logs/app.log` and the session JSON? If a new branch, fallback, or error condition isn't visible in either, add logging before merging.

## Error handling

Error paths must not resemble success paths (Operator Profile §3.3). Specifically:

- **Fallbacks are decisions, not silence.** When a provider call fails and we fall back (e.g., classification timeout → default to `primary`), the session JSON must record the failure *and* the fallback decision. The caller may receive a normal-looking response, but the logs must show it wasn't a normal path.
- **Structured errors over HTTP 200.** If the router itself can't fulfill a request (all backends down, classification permanently broken), return an error response — never an empty or fabricated success. An HTTP 200 with no content is worse than a 502 with a clear message.
- **No catch-all exception swallowing.** Broad `except Exception` blocks are acceptable at the outermost request handler to prevent process crashes, but they must log the full traceback and return an error response. Inner code should catch specific exceptions and handle them explicitly.
