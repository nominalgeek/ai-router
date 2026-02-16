# CLAUDE.md

See [AI_OPERATOR_PROFILE.md](AI_OPERATOR_PROFILE.md) for general AI assistant operating constraints. This project constitution takes precedence where specific.

## Project Overview

Personal homelab proof-of-concept with two goals:

1. **Hybrid local/cloud LLM routing.** Explore how to classify requests by complexity and route them to the right backend — keeping simple/moderate work on local hardware, escalating to cloud APIs only when needed. The hardware is capable but still limited in the real world, so smart routing matters. Develop reusable patterns for future projects (e.g., Mindcraft bot, Home Assistant integrations).

2. **Automated improvement via session logs.** Build toward a closed loop where an autonomous agent consumes session logs from real usage (via Open WebUI), identifies poor routing decisions or weak responses, and either improves prompt templates directly or documents weaknesses for human review.

Exposes an OpenAI-compatible API so any client that speaks the OpenAI format can use it transparently.

A small classifier model (Nemotron Mini 4B) evaluates each request and assigns one of five routes:

| Route | Backend | When |
|-------|---------|------|
| SIMPLE → `router` | Nemotron Mini 4B (local) | Greetings, trivial questions |
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
| **Python** | 3.13 |
| **CUDA** | 13.1 (driver 590.48) |
| **Docker** | 29.2 / Compose 5.0 |

**VRAM allocation (single GPU, shared):**

Both vLLM containers share GPU 0. The split is configured in `docker-compose.yml` via `--gpu-memory-utilization`:

| Container | Model | VRAM budget | Context length |
|-----------|-------|-------------|----------------|
| `vllm-router` | Nemotron Mini 4B (fp8, on-the-fly) | ~10% (~10 GB) | 4,096 tokens |
| `vllm-primary` | Nemotron Nano 30B (fp8 KV cache) | ~80% (~77 GB) | 32,768 tokens |

~10% VRAM remains unallocated as headroom. The router model weights are ~4 GB after fp8 quantization (on-the-fly via `--quantization fp8`), leaving ample KV cache within its 10% budget. The bulk of the GPU budget goes to the primary model's KV cache for long-context requests.

**Models:**

| Role | Source | Notes |
|------|--------|-------|
| Router / classifier | [nvidia/Nemotron-Mini-4B-Instruct](https://huggingface.co/nvidia/Nemotron-Mini-4B-Instruct) | Also handles SIMPLE queries directly |
| Primary | [unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4](https://huggingface.co/unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4) | NVFP4 quantization by Unsloth |
| Cloud (xAI) | `grok-4-1-fast-reasoning` (default) | Configurable via `XAI_MODEL` env var |

Available xAI models (set via `XAI_MODEL` in `.env`):
- `grok-4-1-fast-reasoning` — default, used for COMPLEX and ENRICH routes
- `grok-4-1-fast-non-reasoning` — faster, no chain-of-thought
- `grok-code-fast-1` — code-focused variant

`nano_v3_reasoning_parser.py` is a vLLM reasoning parser plugin for the Nano 30B model's output format. It is sourced from the [Unsloth model page](https://huggingface.co/unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4), not written as part of this project. It is mounted into the primary vLLM container and referenced via `--reasoning-parser-plugin` in `docker-compose.yml`.

## Project Structure

```
router.py                       # Entrypoint — runs src.app
src/
  app.py                        # Flask app, route handlers, pipeline dispatch
  providers.py                  # Route classification, enrichment, request forwarding
  config.py                     # Env vars, prompt loading, constants
  session_logger.py             # Per-request JSON session logs
config/prompts/
  routing/
    system.md                   # Classification system prompt for Mini 4B
    request.md                  # Classification request template
  enrichment/
    system.md                   # Enrichment system prompt (sent to xAI)
    injection.md                # Context injection template (prepended for primary)
  meta/
    system.md                   # Meta pipeline system prompt
docker-compose.yml              # All services: traefik, ai-router, vllm-router, vllm-primary
Makefile                        # Common operations (up, down, test, health, etc.)
traefik/                        # Traefik reverse proxy config
docs/
  architecture.md               # Mermaid architecture diagrams
Benchmark                       # Bash script — latency, throughput, concurrency benchmarks
Test                            # Bash script — integration test suite (health, routing, endpoints)
logs/sessions/                  # Auto-generated per-request JSON session logs
```

## Coding Conventions

- **Readability first.** Write comments that explain both *why* something exists and *what* it does — assume the reader is a human learning how routing systems work, not someone skimming for an API signature.
- **Keep files small.** Look for logical separations and break things up before any single file gets unwieldy. Current split: `config.py` (env/prompts), `providers.py` (classification + forwarding), `app.py` (Flask routes + pipeline orchestration), `session_logger.py` (logging).
- **Keep it followable.** This is a learning project — the code should be simple enough to trace end-to-end without jumping through layers of abstraction. That sometimes tensions with keeping files small; balance by splitting along clear logical boundaries, not by introducing indirection.
- **Externalize configuration from code.** Prompts live in markdown files under `config/prompts/`, env vars drive all connection strings and feature flags, model names are configurable. Changing behavior shouldn't require editing Python when possible.

## Architecture

This is an exploratory project — architecture decisions are working hypotheses, not settled. See `docs/architecture.md` for Mermaid diagrams of the full pipeline.

**Current decisions:**

- **Single Flask process, synchronous.** All routing, enrichment, and forwarding happen in the request thread. Simple to follow and debug. Adequate for single-user homelab load.
- **Classification via prompt, not code.** Routing decisions come from the classifier model responding to a prompt template, not from hardcoded rules. This makes the routing behavior tunable by editing markdown files, but means classification quality depends on the small model's judgment.
- **Enrichment is a two-hop pipeline.** ENRICH queries hit xAI first (for real-time context), then inject that context into the request before forwarding to the primary model. The boundary between "fetch context" and "generate response" is explicit — two separate API calls with the handoff visible in session logs.
- **Session logs as the observability layer.** Every routed request writes a full-lifecycle JSON file. This is the primary way to understand what the system did and why. No metrics, no dashboards — just inspectable files.
- **OpenAI-compatible API as the only interface.** Any client that speaks the OpenAI chat format works transparently. The routing is invisible to the caller. The `/v1/models` endpoint presents a single virtual model (`ai-router` by default, configurable via `VIRTUAL_MODEL` env var) — external consumers like Open WebUI see one model and never know about the backend split.
- **`/stats` endpoint is a labeled placeholder.** It exists in the route table but returns a stub message. It does not pretend to have data.
- **Client inference parameters are passed through (for now).** `forward_request()` currently forwards client-supplied parameters (`temperature`, `top_p`, `max_tokens`, etc.) directly to backends. This means Open WebUI's UI sliders influence generation. Future work: define per-route parameter profiles internally so the router fully owns inference behavior and client settings are ignored or bounded.

## Development

**Always work inside the Python venv:**

```bash
make venv                  # create venv + install deps (first time)
source .venv/bin/activate  # activate before any Python work
```

Dependencies are minimal (`flask`, `requests`) and managed in `requirements.txt`.

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
| `user_query` | The original user message (truncated to 500 chars) |
| `route` | Which route was chosen (`router`, `primary`, `xai`, `enrich`, `meta`) |
| `classification_raw` | The raw classifier output (e.g., `"SIMPLE"`) |
| `classification_ms` | How long classification took |
| `steps[]` | Ordered list of API calls made — each with `provider`, `url`, `model`, `messages_sent`, `response_content`, `duration_ms`, `status` |
| `total_ms` | End-to-end request time |
| `error` | Error message if the request failed, `null` otherwise |

**What to look for when evaluating quality:**

- **Misclassifications**: A `SIMPLE` query that got a poor answer (should have been `MODERATE`), or a `MODERATE` query that went to xAI unnecessarily (wasted cloud call)
- **Enrichment failures**: `enrich` route where the xAI context step returned empty or irrelevant content
- **Latency outliers**: `classification_ms` or step `duration_ms` values that seem abnormally high
- **Meta pipeline issues**: Truncation warnings in logs, or meta-prompts that weren't detected and went through classification instead

The intended workflow: use the router daily via Open WebUI, let session logs accumulate, then have an autonomous agent review them — surfacing issues and either refining `config/prompts/` templates or flagging decisions that need human judgment. Auto-rotation keeps logs to 7 days / 5000 files.
