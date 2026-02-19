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

## Do not change without human review

- **Model names** — Changing models affects the entire routing system (prompts, classification quality, response format). The reasoning parser plugin is model-specific.
- **Port mappings** — The ai-router Flask app, Traefik labels, and test scripts all reference these ports.
- **Network topology** — The `ai-network` bridge and service names are referenced across `Makefile`, `src/config.py`, and test scripts.
- **Cloudflare Tunnel** — The `cloudflared` service provides secure ingress via Cloudflare's edge. Changes to its configuration affect external access. The tunnel token (`CF_TUNNEL_TOKEN`) lives in `.secrets`; public hostname routing is configured in the Cloudflare Zero Trust dashboard, not in this repo. See `docs/cloudflare-tunnel-setup.md`.
- **Container names** — Referenced by health checks, Makefile targets, and monitoring scripts.
- **Docker image versions** — Currently `vllm/vllm-openai:latest`. Pinning to a specific version requires testing; vLLM releases can change flag behavior.
