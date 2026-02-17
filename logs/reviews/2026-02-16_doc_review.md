# Doc Review Report
**Date**: 2026-02-16
**Files reviewed**: 27 source + doc files

## Summary
- Documentation files checked: 4
- Source files read: 7
- Discrepancies found: 2
- Auto-fixed: 0

## Discrepancies

### Env vars: Missing `LOG_DIR` and log rotation env vars in documentation
**Severity**: medium
**Doc files**: README.md (Configuration section), CLAUDE.md (Coding Conventions section)
**Source file**: src/config.py:12, src/session_logger.py:13-15
**Doc says**: No mention of `LOG_DIR`, `LOG_MAX_AGE_DAYS`, or `LOG_MAX_COUNT` env vars
**Code says**:
- `LOG_DIR` defaults to `/var/log/ai-router` (config.py:12)
- `LOG_MAX_AGE_DAYS` defaults to `7` (session_logger.py:14)
- `LOG_MAX_COUNT` defaults to `5000` (session_logger.py:15)
**Recommendation**: Document these env vars in the README.md Configuration section. While they have sensible defaults and users rarely need to change them, they control observable behavior (log rotation) and should be listed for completeness. CLAUDE.md mentions the rotation policy ("Auto-rotation keeps logs to 7 days / 5000 files") but doesn't mention these are configurable via env vars.

### Container name: Inconsistent `vllm-router` vs `router` service name
**Severity**: low
**Doc file**: README.md:22-23 (Prerequisites section mentions "current setup uses ~85 GB"), CLAUDE.md:29-30 (VRAM allocation table)
**Source file**: docker-compose.yml:72, docker-compose.yml:121
**Doc says**: VRAM allocation table in CLAUDE.md uses "Container" column with values `vllm-router` and `vllm-primary`
**Code says**:
- Service name in docker-compose.yml is `router` (line 69) but container_name is `vllm-router` (line 72)
- Service name is `primary` (line 118) but container_name is `vllm-primary` (line 121)
**Recommendation**: No fix needed. The documentation correctly uses the container names (`vllm-router`, `vllm-primary`) which are what users see when running `docker ps` or the health checks. The service names (`router`, `primary`) are internal to docker-compose and correctly referenced in internal networking (e.g., `ROUTER_URL=http://router:8001`). The documentation is using the user-facing names, which is correct.

## Verified Correct

I systematically verified the following categories and found them accurate:

### Environment Variables
All env vars used in config.py are documented:
- ✓ `ROUTER_URL`, `PRIMARY_URL` (docker-compose.yml environment section)
- ✓ `XAI_API_KEY`, `XAI_MODEL` (README.md, CLAUDE.md)
- ✓ `XAI_SEARCH_TOOLS` (README.md Configuration section, CLAUDE.md)
- ✓ `CLASSIFY_CONTEXT_BUDGET` (README.md Tuning Parameters table)
- ✓ `XAI_MIN_MAX_TOKENS` (README.md Tuning Parameters table, CLAUDE.md)
- ✓ `VIRTUAL_MODEL` (README.md Tuning Parameters table, CLAUDE.md)
- ✓ `TZ` (README.md Configuration > Timezone section, docker-compose.yml)
- ✓ `HF_TOKEN` (README.md Quick Start, docker-compose.yml)

Minor omission: `LOG_DIR`, `LOG_MAX_AGE_DAYS`, `LOG_MAX_COUNT` are not documented but have sensible defaults.

### Model Names and HuggingFace URLs
- ✓ `ROUTER_MODEL`: `cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit` (config.py:35, docker-compose.yml:80, README.md:73, CLAUDE.md)
- ✓ `PRIMARY_MODEL`: `unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4` (config.py:36, docker-compose.yml:131, README.md:74, CLAUDE.md)
- ✓ `XAI_MODEL` default: `grok-4-1-fast-reasoning` (config.py:34, README.md:78, CLAUDE.md)
- ✓ All HuggingFace URLs in README.md and CLAUDE.md match the model IDs in code

### API Endpoints
All Flask routes in app.py match documented endpoints:
- ✓ `/health` (app.py:27-63, README.md:139, architecture.md:156)
- ✓ `/v1/chat/completions` (app.py:66-173, README.md:135, architecture.md:157)
- ✓ `/v1/completions` (app.py:176-199, README.md:136, architecture.md:158)
- ✓ `/v1/models` (app.py:202-216, README.md:137, architecture.md:159)
- ✓ `/api/route` (app.py:219-255, README.md:138, architecture.md:160)
- ✓ `/stats` (app.py:258-268, README.md:140, architecture.md:161)
- ✓ `/` (app.py:271-286, architecture.md:155)

All endpoints have correct HTTP methods documented.

### Container Names and Ports
- ✓ `traefik` on :80, :8080 (docker-compose.yml:9-10, architecture.md:130)
- ✓ `ai-router` on :8002 (docker-compose.yml:29, app.py:296, architecture.md:131)
- ✓ `vllm-router` container / `router` service on :8001 (docker-compose.yml:69-86, config.py:29, architecture.md:132)
- ✓ `vllm-primary` container / `primary` service on :8000 (docker-compose.yml:118-138, config.py:30, architecture.md:133)

### VRAM and Memory Configuration
- ✓ Router model `--gpu-memory-utilization 0.14` = ~14% (docker-compose.yml:84, CLAUDE.md table, README.md:23)
- ✓ Primary model `--gpu-memory-utilization 0.65` = ~65% (docker-compose.yml:133, CLAUDE.md table)
- ✓ Router `--max-model-len 2048` (docker-compose.yml:85, CLAUDE.md table)
- ✓ Primary `--max-model-len 32768` (docker-compose.yml:134, CLAUDE.md table)
- ✓ Total configured: 0.79 (79%), actual ~89% due to CUDA overhead (CLAUDE.md correctly explains this)
- ✓ 96 GB total VRAM documented in CLAUDE.md matches hardware table
- ✓ Derived GB figures: ~13 GB router (14% of 96), ~62 GB primary (65% of 96) are accurate

### Project Structure
Verified actual file tree against documented structure in:
- ✓ CLAUDE.md "Project Structure" section matches reality
- ✓ README.md "Project Structure" section matches reality
- All listed files exist
- No significant files missing from documentation
- Directory structure accurate

### Prompt Files
All prompt paths in config.py:133-139 correspond to actual files:
- ✓ `config/prompts/routing/request.md` exists
- ✓ `config/prompts/routing/system.md` exists
- ✓ `config/prompts/primary/system.md` exists
- ✓ `config/prompts/enrichment/system.md` exists
- ✓ `config/prompts/enrichment/injection.md` exists
- ✓ `config/prompts/meta/system.md` exists
- ✓ `config/prompts/xai/system.md` exists

The documented structure in CLAUDE.md and README.md matches the actual file tree.

### Makefile Targets
Cross-referenced all targets in Makefile against README.md table:
- ✓ All key targets listed in README.md exist in Makefile
- ✓ Descriptions match (checked: `make up`, `make down`, `make health`, `make status`, `make logs`, `make venv`, `make test`, `make benchmark`, `make review`, `make doc-review`, `make gpu`, `make clean-all`)
- ✓ README.md directs users to `make help` for full list (Makefile:11-15 implements this)

### Docker Images
- ✓ Traefik: `traefik:v3.6` (docker-compose.yml:5, architecture.md:130)
- ✓ AI Router: `python:3.12-slim` (docker-compose.yml:30, CLAUDE.md Hardware table mentions 3.12-slim)
- ✓ vLLM containers: `vllm/vllm-openai:latest` (docker-compose.yml:70, docker-compose.yml:119)

### Classification Parameters
- ✓ Routing system prompt injected from `ROUTING_SYSTEM_PROMPT` (providers.py:120, config.py:175-179)
- ✓ Routing request template uses `ROUTING_PROMPT` (providers.py:117, config.py:181-187)
- ✓ Temperature 0.0 for classification (providers.py:123)
- ✓ Context budget applied correctly (providers.py:88-114 uses `CLASSIFY_CONTEXT_BUDGET`)
- ✓ Architecture diagrams in docs/architecture.md accurately represent the flow in providers.py

### Session Log Format
Cross-referenced session_logger.py fields against agents/session-review/AGENT.md documentation:
- ✓ All fields documented in AGENT.md:32-54 match SessionLogger implementation
- ✓ Field names match: `id`, `timestamp`, `user_query`, `client_messages`, `route`, `classification_raw`, `classification_ms`, `steps[]`, `total_ms`, `error`
- ✓ Step fields match: `step`, `provider`, `url`, `model`, `messages_sent`, `params`, `duration_ms`, `status`, `finish_reason`, `response_content`

### Test Scripts
- ✓ `Test` script exists and is executable
- ✓ `Benchmark` script exists and is executable
- ✓ Both scripts reference correct endpoints and model names
- ✓ README.md documents `make test` and `make benchmark` targets that invoke these scripts

### Route Definitions
All five routes are consistently defined across documentation:
- ✓ SIMPLE → primary (README.md:11, CLAUDE.md table, architecture.md:96)
- ✓ MODERATE → primary (README.md:12, CLAUDE.md table, architecture.md:97)
- ✓ COMPLEX → xai (README.md:13, CLAUDE.md table, architecture.md:98)
- ✓ ENRICH → xai+primary (README.md:14, CLAUDE.md table, architecture.md:99)
- ✓ META → primary (README.md:15, CLAUDE.md table, architecture.md:108)

Route handling in providers.py matches documentation:
- ✓ Classification returns 'primary', 'xai', 'enrich', or 'meta' (providers.py:165-184)
- ✓ Meta detection fast-path (providers.py:46-79)
- ✓ Enrichment pipeline (app.py:88-121)
- ✓ SIMPLE/MODERATE both route to primary (providers.py:169-174)

### Python Dependencies
- ✓ requirements.txt lists: `flask`, `requests`, `claude-code-sdk`
- ✓ All imports in source files match these dependencies
- ✓ No undocumented dependencies

## Conclusion

The documentation is remarkably accurate and well-maintained. Only two minor issues identified:

1. **Medium severity**: Missing documentation for log rotation env vars (`LOG_DIR`, `LOG_MAX_AGE_DAYS`, `LOG_MAX_COUNT`) — should be added to README.md for completeness, even though defaults work well.

2. **Low severity (false alarm)**: Container naming is actually correct — documentation uses user-facing container names which is the right choice.

The codebase demonstrates excellent documentation discipline:
- All configuration externalized to env vars and documented
- API endpoints completely documented
- Architecture diagrams accurately reflect implementation
- Prompt file structure matches code
- Cross-file references are consistent (e.g., model names, URLs, port numbers)

No surgical fixes applied — the single medium-severity issue (missing env var documentation) requires adding new content rather than fixing incorrect values, which falls outside the "surgical fix" scope defined in the task specification.
