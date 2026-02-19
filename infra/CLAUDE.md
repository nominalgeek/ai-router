# infra/ — Infrastructure Configuration

This directory contains Docker Compose service definitions and supporting documentation for the ai-router deployment.

## What's here

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Service definitions for traefik, cloudflared, ai-router, vllm-router, vllm-primary |
| `vram-requirements.md` | How to calculate VRAM needs (weights, KV cache, overhead) |
| `vllm-flags.md` | Explanation of every vLLM flag used in the compose file |

## Safe to change

These parameters are tuning knobs — adjust them, restart, and monitor session logs for a day to confirm.

- **`--gpu-memory-utilization`** — VRAM budget fraction per container. The two values must sum to less than ~0.85 to leave headroom for CUDA overhead. See `vram-requirements.md` for the math.
- **`--max-model-len`** — Context window size. Must be the same for both models (currently 32768). A shorter classifier context causes silent failures when long conversations hit the router. Check `vram-requirements.md` for the per-token cost before increasing.
- **`--max-num-seqs`** — Max concurrent requests. Currently 3 for both (single-user homelab). Increasing this pre-allocates more KV cache pages.
- **`--kv-cache-dtype`** — KV cache precision. Currently fp8 for both models. Switching to fp16 doubles cache memory but is unnecessary for this use case.
- **Environment variables** in the `ai-router` service (e.g., `XAI_SEARCH_TOOLS`, `XAI_MIN_MAX_TOKENS`).

## Requires sequential restart

Both vLLM containers share a single GPU. Starting them simultaneously causes VRAM allocation conflicts. Always use `make up` (which starts them sequentially) or `make restart-gpu`.

**Never** run `docker compose up -d` directly — use the Makefile targets which enforce the correct startup order:
1. Router first (smaller model, loads in ~30s)
2. Wait for router healthy
3. Primary second (larger model, loads in ~2min)
4. Wait for primary healthy
5. ai-router last (Python, no GPU, instant)

## VRAM budget

Total GPU memory: 96 GB. Current allocation:

| Container | `--gpu-memory-utilization` | Approximate actual usage |
|-----------|---------------------------|-------------------------|
| vllm-router | 0.14 | ~15.5 GB |
| vllm-primary | 0.65 | ~69 GB |
| CUDA overhead | — | ~10 GB |
| **Free** | | **~1.5 GB** |

Before changing memory utilization or context lengths, verify the new values fit using the formulas in `vram-requirements.md`. The key constraint: both containers plus CUDA overhead must fit in 96 GB.

## Container security

All containers are hardened with a defense-in-depth approach:

| Control | traefik | cloudflared | ai-router | vllm-router | vllm-primary |
|---------|---------|-------------|-----------|-------------|--------------|
| `cap_drop: ALL` | yes | yes | yes | yes | yes |
| `cap_add` | `NET_BIND_SERVICE` | — | — | — | — |
| `no-new-privileges` | yes | yes | yes | yes | yes |
| `read_only` | yes | yes | — | — | — |
| `tmpfs` | — | — | — | `/tmp:1G` | `/tmp:2G` |
| Non-root user | — | — | `1000:1000` | — | — |
| Volume mounts `:ro` | `/etc/traefik` | — | source, config | parser plugin | — |

**Docker secrets**: `XAI_API_KEY` is mounted as a file at `/run/secrets/xai_api_key` via compose secrets (sourced from the `XAI_API_KEY` env var, which comes from `--env-file .secrets`). The `read_secret()` helper in `src/config.py` reads from the file first, falling back to `os.environ` for local dev.

**Traefik security middlewares** (applied to ai-router route via Docker labels):
- `security-headers` — `frameDeny`, `contentTypeNosniff`, `browserXssFilter`, `referrerPolicy`
- `rate-limit` — 100 req/s average, burst 200

**Traefik dashboard** is disabled (`--api.dashboard=false`). Port 80 is bound to `127.0.0.1` only — external access flows through the Cloudflare Tunnel.

**vLLM containers run as root** because vLLM's internal processes expect write access to `/root/.cache`. The `cap_drop: ALL` + `no-new-privileges` combination limits what root can do inside the container. `tmpfs` mounts provide writable scratch space without persisting to disk.

## trust-remote-code mitigations

Both vLLM containers use `--trust-remote-code`, which executes arbitrary Python from the model's Hugging Face repo. Four layered mitigations reduce this risk:

1. **Pinned revisions** — `--revision <sha>` locks each model to a specific commit. The SHAs are defined in both `docker-compose.yml` (vLLM flags) and `Makefile` (download target). They must match.
2. **Pre-download** — `make download-models` pulls models into the `hf-cache` volume via a one-shot container *before* vLLM starts. This makes the download step explicit, auditable, and separate from inference.
3. **Offline mode** — `HF_HUB_OFFLINE=1` prevents vLLM from contacting Hugging Face at runtime. If the model isn't in the cache, it fails loudly instead of silently downloading.
4. **Network isolation** — vLLM containers are on `ai-internal`, a Docker network with `internal: true` (no internet gateway). Even if `HF_HUB_OFFLINE` were bypassed, the containers cannot reach external hosts.

**To update a model revision:**
1. Update the SHA in both `Makefile` (variables) and `docker-compose.yml` (`--revision` flag)
2. Run `make download-models` to fetch the new revision
3. Restart the affected vLLM container

## Do not change without human review

- **Model names** — Changing models affects the entire routing system (prompts, classification quality, response format). The reasoning parser plugin is model-specific.
- **Model revision SHAs** — Pinned in both `docker-compose.yml` and `Makefile`. Must be updated together. See "trust-remote-code mitigations" above.
- **Port mappings** — The ai-router Flask app, Traefik labels, and test scripts all reference these ports.
- **Network topology** — Two networks: `ai-network` (Traefik, cloudflared, ai-router) and `ai-internal` (vLLM containers, ai-router). The ai-router bridges both. Service names are referenced across `Makefile`, `src/config.py`, and test scripts.
- **Cloudflare Tunnel** — The `cloudflared` service provides secure ingress via Cloudflare's edge. Changes to its configuration affect external access. The tunnel token (`CF_TUNNEL_TOKEN`) lives in `.secrets`; public hostname routing is configured in the Cloudflare Zero Trust dashboard, not in this repo. See `docs/cloudflare-tunnel-setup.md`.
- **Container names** — Referenced by health checks, Makefile targets, and monitoring scripts.
- **Docker image versions** — Currently `vllm/vllm-openai:latest`. Pinning to a specific version requires testing; vLLM releases can change flag behavior.
