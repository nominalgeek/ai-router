# CLAUDE.md

See [AI_OPERATOR_PROFILE.md](AI_OPERATOR_PROFILE.md) for general AI assistant operating constraints. This project constitution takes precedence where specific.

## Project Overview

Personal homelab proof-of-concept with two goals:

1. **Hybrid local/cloud LLM routing.** Explore how to classify requests by complexity and route them to the right backend — keeping simple/moderate work on local hardware, escalating to cloud APIs only when needed. The hardware is capable but still limited in the real world, so smart routing matters. Develop reusable patterns for future projects (e.g., Mindcraft bot, Home Assistant integrations).

2. **Automated improvement via session logs.** Build toward a closed loop where an autonomous agent consumes session logs from real usage (via Open WebUI), identifies poor routing decisions or weak responses, and either improves prompt templates directly or documents weaknesses for human review.

Exposes an OpenAI-compatible API so any client that speaks the OpenAI format can use it transparently.

A classifier model (Nemotron Orchestrator 8B AWQ) evaluates each request and assigns one of five routes:

| Route | Backend | When |
|-------|---------|------|
| SIMPLE → `primary` | Nemotron Nano 30B (local) | Greetings, trivial questions |
| MODERATE → `primary` | Nemotron Nano 30B (local) | Coding, analysis, explanations |
| COMPLEX → `xai` | Grok (xAI API) | Research-level, novel problems |
| ENRICH → `xai` + `primary` | Grok → Nano 30B | Queries needing real-time/web data |
| META → `primary` | Nano 30B (local) | Client-generated meta-prompts (follow-up suggestions, title generation, summaries) — skips classification |

Stack: Python/Flask, Docker Compose, vLLM (local model serving), Traefik (reverse proxy).

## Hardware Environment

All local inference runs on a single workstation. Development and deployment decisions are bound by these specs.

| Component | Spec |
|-----------|------|
| **GPU** | NVIDIA RTX PRO 6000 Blackwell Workstation Edition — 96 GB VRAM |
| **CPU** | AMD Ryzen 9 9950X3D — 16 cores / 32 threads |
| **RAM** | 92 GB DDR5 |
| **Storage** | 2x Samsung 990 PRO 2TB NVMe + 1x Crucial T700 1TB NVMe |
| **OS** | Debian 13 (Trixie) |
| **Python** | 3.13 (host) / 3.12-slim (ai-router container) |
| **CUDA** | 13.1 (driver 590.48) |
| **Docker** | 29.2 / Compose 5.0 |

**VRAM allocation (single GPU, shared):**

Both vLLM containers share GPU 0. The split is configured in `infra/docker-compose.yml` via `--gpu-memory-utilization`:

| Container | Model | VRAM budget | Context length |
|-----------|-------|-------------|----------------|
| `vllm-router` | Nemotron Orchestrator 8B (AWQ 4-bit) | ~14% (~13 GB) | 32,768 tokens |
| `vllm-primary` | Nemotron Nano 30B (fp8 KV cache) | ~65% (~62 GB) | 32,768 tokens |

Both models use the same `--max-model-len 32768`. This is a deliberate rule: **keep context length uniform across all local models.** A shorter classifier context causes silent failures (HTTP 400s) when long conversations hit the router — the classifier truncates or rejects input the primary would handle fine. Matching context lengths eliminates this class of bug entirely, and the 8B AWQ model fits comfortably within its 14% VRAM budget at 32K thanks to fp8_e4m3 KV cache.

Configured total is 0.79 (14% + 65%), but actual VRAM usage is ~89% due to CUDA context overhead (~10%). This leaves ~11% (~10 GB) free as headroom. The router model weights are ~6 GB after AWQ 4-bit quantization; fp8_e4m3 KV cache and prefix caching keep memory efficient within the 14% budget. The primary also uses fp8 KV cache, which lets it maintain 32K context within its reduced 65% budget.

**Models:**

| Role | Source | Notes |
|------|--------|-------|
| Router / classifier | [cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit](https://huggingface.co/cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit) | Classification only — purpose-built for routing |
| Primary | [unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4](https://huggingface.co/unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4) | NVFP4 quantization by Unsloth |
| Cloud (xAI) | `grok-4-1-fast-reasoning` (default) | Configurable via `XAI_MODEL` env var |

Available xAI models (set via `XAI_MODEL` in `.env` — non-secret config):
- `grok-4-1-fast-reasoning` — default, used for COMPLEX and ENRICH routes
- `grok-4-1-fast-non-reasoning` — faster, no chain-of-thought
- `grok-code-fast-1` — code-focused variant

`nano_v3_reasoning_parser.py` is a vLLM reasoning parser plugin for the Nano 30B model's output format. It is sourced from the [Unsloth model page](https://huggingface.co/unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4), not written as part of this project. It is mounted into the primary vLLM container and referenced via `--reasoning-parser-plugin` in `infra/docker-compose.yml`.

## Project Structure

```
router.py                       # Entrypoint — runs src.app
src/
  CLAUDE.md                     # Python source guardrails (externalization, separation of concerns)
  app.py                        # Flask app, route handlers, pipeline dispatch
  providers.py                  # Route classification, enrichment, request forwarding
  config.py                     # Env vars, prompt loading, constants
  session_logger.py             # Per-request JSON session logs
config/prompts/
  primary/
    system.md                   # Base system prompt injected into every forwarded request
  routing/
    system.md                   # Classification system prompt for Orchestrator 8B
    request.md                  # Classification request template
    truncation_note.md          # Note injected when the query is truncated for classification
  xai/
    system.md                   # xAI system prompt (COMPLEX route — conciseness guidance)
  enrichment/
    system.md                   # Enrichment system prompt (sent to xAI for context retrieval)
    injection.md                # Context injection template (prepended for primary)
  meta/
    system.md                   # Meta pipeline system prompt
infra/
  CLAUDE.md                     # Infrastructure guardrails (VRAM budget, restart rules)
  docker-compose.yml            # All services: traefik, cloudflared, ai-router, vllm-router, vllm-primary
  vram-requirements.md          # VRAM calculation guide (weights, KV cache, overhead)
  vllm-flags.md                 # Explanation of every vLLM flag in the compose file
Makefile                        # Common operations (up, down, test, health, etc.)
traefik/                        # Traefik reverse proxy config
docs/
  architecture.md               # Mermaid architecture diagrams
  cloudflare-tunnel-setup.md    # Cloudflare Tunnel + Zero Trust setup guide
agents/
  session-review/
    AGENT.md                    # Task spec for session-review agent (Session CEO in boardroom)
  doc-review/
    AGENT.md                    # Task spec for doc-review agent (QA Validator in boardroom)
  challenger/
    AGENT.md                    # Task spec for adversarial challenger agent
  boardroom_run.py              # Orchestrator for the Improvement Board cycle
review-board.yaml               # Improvement Board config (roles, rules, decision lineage)
nano_v3_reasoning_parser.py     # vLLM reasoning parser plugin for Nano 30B (from Unsloth)
AI_OPERATOR_PROFILE.md          # General AI assistant operating constraints
requirements.txt                # Python dependencies
Benchmark                       # Bash script — latency, throughput, concurrency benchmarks
Test                            # Bash script — integration test suite (health, routing, endpoints)
.env                            # Non-sensitive config (TZ, XAI_MODEL, XAI_SEARCH_TOOLS)
.env.example                    # Template for .env
.secrets                        # API keys and tokens (gitignored, chmod 600)
.secrets.example                # Template for .secrets
logs/sessions/                  # Auto-generated per-request JSON session logs
```

## Coding Conventions

- **Occam's razor.** The simplest explanation, implementation, and outcome that solves the problem is the right one. Don't invent configuration knobs when removing a parameter works. Don't add abstraction layers when a flat function is clear. Don't build for hypothetical futures — solve the problem in front of you with the fewest moving parts. When reviewing a design, ask: "What can I remove?" before "What should I add?" This applies equally to code, architecture, prompts, and documentation.
- **Readability first.** Write comments that explain both *why* something exists and *what* it does — assume the reader is a human learning how routing systems work, not someone skimming for an API signature.
- **Keep files small.** Look for logical separations and break things up before any single file gets unwieldy. Current split: `config.py` (env/prompts), `providers.py` (classification + forwarding), `app.py` (Flask routes + pipeline orchestration), `session_logger.py` (logging).
- **Keep it followable.** This is a learning project — the code should be simple enough to trace end-to-end without jumping through layers of abstraction. That sometimes tensions with keeping files small; balance by splitting along clear logical boundaries, not by introducing indirection.
- **Externalize configuration from code.** Prompts live in markdown files under `config/prompts/`, env vars drive all connection strings and feature flags, model names are configurable. Changing behavior shouldn't require editing Python when possible. This applies to *all* natural-language instructions sent to models — including behavioral guidance like "don't echo the system prompt." If it's a sentence a model reads, it belongs in a prompt file, not a Python string. **Secrets live in `.secrets`, not `.env`.** API keys and tokens (`HF_TOKEN`, `XAI_API_KEY`) go in `.secrets` (gitignored, `chmod 600`). Non-sensitive config (`TZ`, `XAI_MODEL`, `XAI_SEARCH_TOOLS`) goes in `.env`. The Makefile passes both via `--env-file .env --env-file .secrets` to Docker Compose. **One deliberate exception:** `load_prompt_file()` in `config.py` accepts a hardcoded fallback string per prompt. These exist solely as a safety net so the service degrades gracefully (with a logged error) if a prompt file is missing — e.g. when running outside Docker without the `config/` volume. The authoritative prompts are always the markdown files; fallbacks should never be the primary path and should be kept roughly in sync with their corresponding files.
- **Strict separation of concerns.** Each component has one job. The router model classifies — it does not generate responses. The primary model generates — it does not classify. The enrichment pipeline fetches context — it does not answer questions. The Flask app orchestrates — it does not contain business logic. This isn't just good architecture; it's a defense against complexity creep. When an AI assistant (or a human in a flow state) is generating code, the temptation is to let responsibilities blur — "just add this one thing here." Resist. If a change crosses a boundary, it belongs in a different component. Every function, file, and service should be explainable in one sentence. If it can't be, it's doing too much.

## Architecture

This is an exploratory project — architecture decisions are working hypotheses, not settled. See `docs/architecture.md` for Mermaid diagrams of the full pipeline.

**Current decisions:**

- **Single Flask process, synchronous.** All routing, enrichment, and forwarding happen in the request thread. Simple to follow and debug. Adequate for single-user homelab load.
- **Router model is classifier-only.** The Orchestrator 8B model only classifies queries — it never generates responses. Both SIMPLE and MODERATE queries route to the primary model. This decouples classification quality from generation quality and enables swapping to purpose-built routing models without affecting response quality.
- **Classification via prompt, not code.** Routing decisions come from the classifier model responding to a prompt template, not from hardcoded rules. This makes the routing behavior tunable by editing markdown files, but means classification quality depends on the small model's judgment.
- **Enrichment is a two-hop pipeline.** ENRICH queries hit xAI first (for real-time context), then inject that context into the request before forwarding to the primary model. The boundary between "fetch context" and "generate response" is explicit — two separate API calls with the handoff visible in session logs.
- **Session logs as the observability layer.** Every routed request writes a full-lifecycle JSON file. This is the primary way to understand what the system did and why. No metrics, no dashboards — just inspectable files.
- **OpenAI-compatible API as the only interface.** Any client that speaks the OpenAI chat format works transparently. The routing is invisible to the caller. The `/v1/models` endpoint presents a single virtual model (`ai-router` by default, configurable via `VIRTUAL_MODEL` env var) — external consumers like Open WebUI see one model and never know about the backend split.
- **`/stats` endpoint is a labeled placeholder.** It exists in the route table but returns a stub message. It does not pretend to have data.
- **The router owns `max_tokens`, not the client.** For all local models (both the classifier and the primary), `max_tokens` is stripped entirely — they generate until their natural stop token, bounded only by vLLM's `--max-model-len` (32K for both). Both are reasoning models that burn tokens on `<think>` blocks; any artificial cap risks truncating reasoning before useful output is emitted. For xAI, a floor (`XAI_MIN_MAX_TOKENS`, default 16K) prevents Open WebUI's low defaults from truncating answers. Other inference parameters (`temperature`, `top_p`, etc.) are still passed through from the client.
- **Route-specific system prompts.** Each route gets its own behavioral prompt: `config/prompts/primary/system.md` for local model routes, `config/prompts/xai/system.md` for the COMPLEX route. Both emphasize conciseness. The primary prompt also includes reasoning guidance to keep `<think>` blocks focused. The enrichment and meta pipelines have their own prompts as before.

## Development

**Always work inside the Python venv:**

```bash
make venv                  # create venv + install deps (first time)
source .venv/bin/activate  # activate before any Python work
```

Dependencies are minimal (`flask`, `requests`, `claude-code-sdk`) and managed in `requirements.txt`.

**Running services:**

```bash
make up          # start all containers (traefik, ai-router, vllm-router, vllm-primary)
make health      # verbose health check of all services
make status      # one-line health summary
make logs        # follow all logs; also: make logs-router, make logs-primary, make logs-ai
```

Run `make help` for the full target list.

**Testing:**

- `make test` — runs the `./Test` bash script (integration tests against the live stack)
- `make benchmark` — runs `./Benchmark` (latency, throughput, concurrency, streaming)
- Both require services to be running (`make up` first)

**Session logs — the feedback loop:**

Every routed request writes a JSON session file to `logs/sessions/`. These are designed to be consumed by both humans and LLM agents.

Each session file contains:

| Field | What it tells you |
|-------|-------------------|
| `id` | Unique session identifier (8-char hex) |
| `timestamp` | ISO 8601 timestamp of the request |
| `client_ip` | Client's real IP address (resolved via proxy headers) |
| `user_query` | The original user message (truncated to 500 chars) |
| `client_messages` | Full original messages array from the client |
| `route` | Which route was chosen (`primary`, `xai`, `enrich`, `meta`) |
| `classification_raw` | The raw classifier output (e.g., `"SIMPLE"`) |
| `classification_ms` | How long classification took |
| `steps[]` | Ordered list of API calls made — each with `step`, `provider`, `url`, `model`, `messages_sent`, `params`, `response_content`, `duration_ms`, `status`, `finish_reason` |
| `total_ms` | End-to-end request time |
| `error` | Error message if the request failed, `null` otherwise |

**What to look for when evaluating quality:**

- **Misclassifications**: A `MODERATE` query that went to xAI unnecessarily (wasted cloud call), or a `COMPLEX` query that stayed local (poor answer quality)
- **Enrichment failures**: `enrich` route where the xAI context step returned empty or irrelevant content
- **Latency outliers**: `classification_ms` or step `duration_ms` values that seem abnormally high
- **Meta pipeline issues**: Truncation warnings in logs, or meta-prompts that weren't detected and went through classification instead

**Validating infrastructure changes:**

When changing vLLM parameters, prompt templates, or other config that affects what the models see, always check session logs after deploying to confirm nothing broke. Things like `--max-model-len` or prompt template size interact — a change to one can silently break another if the total token count exceeds the budget. Let real traffic run for a day, then look for HTTP 400s, classification errors, or truncation warnings in the session files.

The intended workflow: use the router daily via Open WebUI, let session logs accumulate, then have an autonomous agent review them — surfacing issues and either refining `config/prompts/` templates or flagging decisions that need human judgment. Auto-rotation keeps logs to 7 days / 5000 files. See `agents/session-review/AGENT.md` for the full agent task specification.
