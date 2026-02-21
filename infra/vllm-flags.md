# vLLM Flags Reference

Every `--flag` used in the vLLM service commands in `docker-compose.yml`, explained.

See `vram-requirements.md` for the math behind memory allocation decisions.

## Router (Orchestrator 8B — classifier only)

```
--model cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit
```
Which model to load. This is an AWQ 4-bit quantized version of Nemotron Orchestrator 8B, purpose-built for query classification. It only emits routing labels (MODERATE, COMPLEX, ENRICH) — it never generates user-facing responses.

```
--dtype half
```
Compute precision for model weights. `half` = float16. Required for AWQ quantized models — vLLM dequantizes AWQ weights to fp16 for computation. Cannot use `auto` (which would pick bfloat16) because AWQ kernels expect fp16.

```
--kv-cache-dtype fp8_e4m3
```
Store the KV cache in fp8 (1 byte per element) instead of fp16 (2 bytes). Halves KV cache memory with minimal quality impact. Uses the E4M3 fp8 format (4 exponent bits, 3 mantissa bits) which has slightly more precision than E5M2. Fine for a classifier that only emits single-word labels.

```
--calculate-kv-scales
```
Automatically compute the scaling factors needed for fp8 KV cache quantization. Without this, vLLM would need pre-computed scales baked into the model checkpoint. Required when using `--kv-cache-dtype fp8_e4m3` with a model that doesn't ship fp8 scales (most AWQ models don't).

```
--gpu-memory-utilization 0.14
```
vLLM pre-allocates this fraction of total GPU memory (0.14 = 14% of 96 GB = ~13.4 GB). Covers model weights (~6 GB), KV cache, and overhead. The actual VRAM usage may exceed this slightly due to CUDA context and activation memory. This value is set to share GPU 0 with the primary model (which gets 0.65).

```
--max-model-len 2048
```
Maximum sequence length (in tokens) the model will accept. Sets the upper bound on input + output tokens per request. The classifier prompt (system prompt + conversation context + request template + user query) typically totals 800-1,300 tokens, leaving room for the model's `<think>` reasoning before emitting its classification label. Increasing this requires more KV cache memory — see `vram-requirements.md` for the per-token cost (64 KB/token for this model).

```
--max-num-seqs 3
```
Maximum number of requests vLLM will process concurrently. Set to 3 for this single-user homelab — classification requests are short-lived and rarely overlap. Lower values reduce KV cache pre-allocation, freeing VRAM.

```
--port 8001
```
HTTP port for the OpenAI-compatible API inside the container. The ai-router Flask app connects to `http://router:8001`.

```
--trust-remote-code
```
Allow execution of custom Python code bundled with the model (e.g., custom attention implementations, tokenizer code). Required by many HuggingFace models that include custom modeling files. The model is from a known source (cyankiwi's AWQ quantization of an NVIDIA model).

```
--enable-prefix-caching
```
Cache KV computations for shared prompt prefixes across requests. Since every classification request starts with the same system prompt, this avoids recomputing those KV entries. Saves compute at the cost of some memory for the prefix cache.

```
--disable-log-stats
```
Suppress periodic performance statistics logging (tokens/sec, queue depth, etc.). Reduces log noise in a homelab setup where these metrics aren't monitored.

## Primary (Nemotron Nano 30B — response generation)

```
--model unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
```
Which model to load. Nemotron Nano 30B with NVFP4 quantization by Unsloth. This is a Mixture-of-Experts (MoE) model — 30B total parameters but only ~3B active per token. All 30B parameters must fit in VRAM; only the compute cost is reduced.

```
--kv-cache-dtype fp8
```
Store the KV cache in fp8. Same rationale as the router — halves cache memory. Uses the default fp8 format (E4M3 on NVIDIA GPUs). This is what allows the model to maintain 32K context within the 65% VRAM budget.

```
--gpu-memory-utilization 0.65
```
65% of 96 GB = ~62 GB pre-allocated for this model. Covers weights (~18 GB NVFP4), KV cache, and overhead. Combined with the router's 0.14, the total configured utilization is 0.79 — actual usage runs ~89% due to CUDA context overhead.

```
--max-model-len 32768
```
32K token context window. Supports long conversations and detailed responses. The model's native context length is 128K, but 32K is sufficient for this use case and keeps VRAM manageable. The KV cache cost is ~17 KB/token for this model — see `vram-requirements.md`.

```
--max-num-seqs 3
```
Maximum concurrent requests. Set to 3 for single-user homelab use. Reduces KV cache pre-allocation compared to vLLM's default of 256.

```
--disable-log-stats
```
Same as router — suppress periodic stats logging.

```
--port 8000
```
HTTP port inside the container. The ai-router connects to `http://primary:8000`.

```
--trust-remote-code
```
Same as router — required for the model's custom code.

```
--reasoning-parser-plugin /app/nano_v3_reasoning_parser.py
```
Path to a custom vLLM plugin that parses the Nano 30B's reasoning output format. The model wraps its chain-of-thought in `<think>...</think>` tags, and this plugin separates reasoning from the final answer so vLLM can populate both `reasoning_content` and `content` fields in the OpenAI-compatible response. The plugin file is mounted from the project root via a Docker volume.

```
--reasoning-parser nano_v3
```
Tells vLLM which parser to use (by name) from the loaded plugin. Works in conjunction with `--reasoning-parser-plugin` above.

## Environment Variables (not vLLM flags, but relevant)

These are set in the `environment:` section of the compose services, not as vLLM command-line flags, but they affect vLLM behavior:

### Router
| Variable | Value | Purpose |
|----------|-------|---------|
| `VLLM_USE_MODELSCOPE` | `false` | Download models from HuggingFace, not ModelScope |

### Primary
| Variable | Value | Purpose |
|----------|-------|---------|
| `VLLM_USE_MODELSCOPE` | `false` | Download models from HuggingFace, not ModelScope |
| `VLLM_USE_FLASHINFER_MOE_FP4` | `1` | Enable FlashInfer's optimized FP4 MoE kernels for faster expert computation |
| `VLLM_FLASHINFER_MOE_BACKEND` | `throughput` | Optimize FlashInfer MoE for throughput over latency (better for longer generations) |

### Both
| Variable | Value | Purpose |
|----------|-------|---------|
| `HF_TOKEN` | from `.secrets` | HuggingFace authentication token for gated model downloads |
| `LD_LIBRARY_PATH` | `/usr/lib/x86_64-linux-gnu` | Ensures CUDA libraries are found by the vLLM container |
