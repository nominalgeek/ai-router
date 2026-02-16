# ai-router

Homelab experimentation with LLM request routing. Intelligently routes incoming OpenAI-compatible API requests to different model backends based on query complexity.

## How It Works

A small classifier model (Nemotron Mini 4B) evaluates each request and routes it to one of four paths:

| Classification | Backend | Model | Use Case |
|---|---|---|---|
| SIMPLE | Router (local) | Nemotron Mini 4B | Greetings, basic questions |
| MODERATE | Primary (local) | Nemotron Nano 30B | Coding, explanations, analysis |
| COMPLEX | xAI API | Grok | Research-level, novel problems |
| ENRICH | xAI + Primary | Grok + Nano 30B | Anything needing current/real-time data |

## Prerequisites

- NVIDIA GPU with at least 24GB VRAM
- Docker with NVIDIA Container Toolkit
- `docker compose` (v2)
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

## Local Development

Set up a Python virtual environment for local development and tooling:

```bash
make venv
source .venv/bin/activate
```

Dependencies are managed in `requirements.txt`.

## Project Structure

```
src/
  config.py            # Environment variables, prompt loading
  session_logger.py    # Per-request JSON session logs
  providers.py         # Routing logic, enrichment, request forwarding
  app.py               # Flask app and route handlers
router.py              # Entrypoint (runs src.app)
config/prompts/
  routing/
    system.md          # Classification system prompt
    request.md         # Classification request template
  enrichment/
    system.md          # Enrichment system prompt (sent to xAI)
    injection.md       # Enrichment context injection template (sent to primary)
docker-compose.yml     # All services: traefik, ai-router, router model, primary model
Makefile               # Common operations
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
ls -lt logs/sessions/ | head 10

# Inspect a session
cat logs/sessions/<filename>.json | python -m json.tool

# Find all requests routed to xAI
grep -l '"route": "xai"' logs/sessions/*.json
```

Logs auto-rotate: files older than 7 days or exceeding 5000 total are cleaned up automatically. Timestamps use the configured `TZ` timezone (default: `America/Los_Angeles`).

## Timezone

All timestamps (session logs, date injection into prompts) use the `TZ` environment variable. Defaults to `America/Los_Angeles` (US Pacific). Set in `.env`:

```
TZ=America/Los_Angeles
```

Uses standard [IANA timezone names](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

## Enrichment Search Tools

When a query is classified as ENRICH, the router calls xAI's `/v1/responses` API with search tools enabled so Grok can fetch real-time data from the web and X (Twitter) before responding.

Configure which tools are available via the `XAI_SEARCH_TOOLS` environment variable:

| Value | Behavior |
|---|---|
| `web_search,x_search` | Both web and X search enabled (default) |
| `web_search` | Web search only |
| `x_search` | X search only |
| *(empty)* | No search tools — Grok answers from training data only |

Set in `.env`:
```
XAI_SEARCH_TOOLS=web_search,x_search
```

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
| `make test` | Run full test suite |
| `make gpu` | Show GPU status |
| `make clean-all` | Remove everything including model cache volumes |
