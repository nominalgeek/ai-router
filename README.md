# ai-router

Homelab experimentation with LLM request routing. Intelligently routes incoming OpenAI-compatible API requests to different model backends based on query complexity.

## How It Works

A classifier model ([Nemotron Orchestrator 8B AWQ](https://huggingface.co/cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit)) evaluates each request and assigns one of five routes:

| Route | Backend | Model | Use Case |
|---|---|---|---|
| SIMPLE | Primary (local) | Nemotron Nano 30B | Greetings, trivial questions |
| MODERATE | Primary (local) | Nemotron Nano 30B | Coding, analysis, explanations |
| COMPLEX | xAI API | Grok | Research-level, novel problems |
| ENRICH | xAI + Primary | Grok → Nano 30B | Queries needing real-time/web data |
| META | Primary (local) | Nemotron Nano 30B | Client-generated meta-prompts (skips classification) |

The classifier only classifies — it never generates responses. Both SIMPLE and MODERATE route to the same primary model. The META route auto-detects client-generated meta-prompts (follow-up suggestions, title generation, summaries) and bypasses classification entirely.

Exposes an OpenAI-compatible API so any client that speaks the OpenAI format (e.g., Open WebUI) can use it transparently. The `/v1/models` endpoint presents a single virtual model (`ai-router` by default, configurable via `VIRTUAL_MODEL`).

## Prerequisites

- NVIDIA GPU with sufficient VRAM (current setup uses ~85 GB on an RTX PRO 6000)
- Docker with NVIDIA Container Toolkit
- `docker compose` (v2+)
- (Optional) xAI API key for complex/enrich routing

## Quick Start

1. **Clone and configure**

   ```bash
   git clone <repo-url> && cd ai-router
   cp .env.example .env   # or create .env manually
   ```

   Add your keys to `.env`:
   ```
   HF_TOKEN=<your-huggingface-token>
   XAI_API_KEY=<your-xai-api-key>           # optional
   XAI_SEARCH_TOOLS=web_search,x_search     # optional, see below
   XAI_MODEL=grok-4-1-fast-reasoning        # optional, see below
   TZ=America/Los_Angeles                   # timezone, defaults to US Pacific
   ```

2. **Start services**

   ```bash
   make up
   ```

   First run will download models (~20GB). Monitor progress with `make logs`.
   The vLLM containers take a few minutes to load models and become healthy.

3. **Verify everything is running**

   ```bash
   make health
   ```

4. **Send a request**

   ```bash
   curl -s http://localhost/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"messages": [{"role": "user", "content": "Hello!"}]}' | jq .
   ```

## Models

| Role | Model | Notes |
|---|---|---|
| Classifier | [cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit](https://huggingface.co/cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit) | Classification only — purpose-built for routing |
| Primary | [unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4](https://huggingface.co/unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4) | NVFP4 quantization by Unsloth |
| Cloud | Grok (xAI API) | Configurable via `XAI_MODEL` env var |

Available xAI models (set via `XAI_MODEL` in `.env`):
- `grok-4-1-fast-reasoning` — default, used for COMPLEX and ENRICH routes
- `grok-4-1-fast-non-reasoning` — faster, no chain-of-thought
- `grok-code-fast-1` — code-focused variant

## Local Development

Set up a Python virtual environment for local development and tooling:

```bash
make venv
source .venv/bin/activate
```

Dependencies are managed in `requirements.txt`.

## Project Structure

```
router.py                       # Entrypoint (runs src.app)
src/
  app.py                        # Flask app and route handlers
  providers.py                  # Routing logic, enrichment, request forwarding
  config.py                     # Environment variables, prompt loading
  session_logger.py             # Per-request JSON session logs
config/prompts/
  primary/
    system.md                   # Base system prompt injected into every request
  routing/
    system.md                   # Classification system prompt for Orchestrator 8B
    request.md                  # Classification request template
  enrichment/
    system.md                   # Enrichment system prompt (sent to xAI)
    injection.md                # Context injection template (prepended for primary)
  xai/
    system.md                   # xAI system prompt (COMPLEX route)
  meta/
    system.md                   # Meta pipeline system prompt
docker-compose.yml              # All services: traefik, ai-router, vllm-router, vllm-primary
nano_v3_reasoning_parser.py     # vLLM reasoning parser plugin for Nano 30B
Makefile                        # Common operations
traefik/                        # Traefik reverse proxy config
docs/
  architecture.md               # Mermaid architecture diagrams
agents/
  session-review/
    AGENT.md                    # Task spec for autonomous session-review agent
  doc-review/
    AGENT.md                    # Task spec for documentation-review agent
Test                            # Integration test suite (bash)
Benchmark                       # Latency, throughput, concurrency benchmarks (bash)
logs/sessions/                  # Auto-generated per-request JSON session logs
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completions with auto-routing (OpenAI-compatible) |
| `/v1/completions` | POST | Legacy completions |
| `/v1/models` | GET | List available models |
| `/api/route` | POST | Explicit routing control for testing |
| `/health` | GET | Service health check |
| `/stats` | GET | Routing statistics (placeholder) |

## Session Logs

Every routed request produces a JSON session file in `logs/sessions/` capturing the full request lifecycle: classification decision, messages sent to each provider, response content, and timing. Useful for debugging routing behavior.

```bash
# List recent sessions
ls -lt logs/sessions/ | head -10

# Inspect a session
cat logs/sessions/<filename>.json | python -m json.tool

# Find all requests routed to xAI
grep -l '"route": "xai"' logs/sessions/*.json
```

Logs auto-rotate: files older than 7 days or exceeding 5000 total are cleaned up automatically. Timestamps use the configured `TZ` timezone (default: `America/Los_Angeles`).

## Configuration

### Timezone

All timestamps (session logs, date injection into prompts) use the `TZ` environment variable. Defaults to `America/Los_Angeles` (US Pacific). Uses standard [IANA timezone names](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

### Enrichment Search Tools

When a query is classified as ENRICH, the router calls xAI's `/v1/responses` API with search tools enabled so Grok can fetch real-time data from the web and X (Twitter) before responding.

Configure which tools are available via the `XAI_SEARCH_TOOLS` environment variable:

| Value | Behavior |
|---|---|
| `web_search,x_search` | Both web and X search enabled (default) |
| `web_search` | Web search only |
| `x_search` | X search only |
| *(empty)* | No search tools — Grok answers from training data only |

### Tuning Parameters

These env vars control classification and enrichment behavior. Defaults work well out of the box.

| Variable | Default | Description |
|---|---|---|
| `CLASSIFY_CONTEXT_BUDGET` | `2000` | Max chars of conversation history sent to classifier |
| `XAI_MIN_MAX_TOKENS` | `16384` | Floor for max_tokens on xAI requests (prevents client low defaults) |
| `VIRTUAL_MODEL` | `ai-router` | Model name exposed via `/v1/models` |

## Makefile Targets

Run `make help` to see all available targets. Key ones:

| Target | Description |
|---|---|
| `make up` | Start all services |
| `make down` | Stop all services |
| `make health` | Verbose health check of all services |
| `make status` | One-line health summary |
| `make logs` | Follow logs for all services |
| `make venv` | Create Python venv and install dependencies |
| `make test` | Run integration test suite |
| `make benchmark` | Run latency/throughput/concurrency benchmarks |
| `make review` | Run session-review agent on accumulated logs |
| `make doc-review` | Run doc-review agent to check docs against code |
| `make gpu` | Show GPU status |
| `make clean-all` | Remove everything including model cache volumes |
