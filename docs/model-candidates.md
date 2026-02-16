# Router Model Candidates

The current router/classifier is **Nemotron Mini 4B** (`nvidia/Nemotron-Mini-4B-Instruct`). It works, but it's a general-purpose instruct model repurposed for classification — not purpose-built for routing. This document tracks alternative models worth evaluating.

The constraints for any router model candidate:
- Must fit within ~10% of 96 GB VRAM (~10 GB budget) alongside the primary model
- Must respond fast enough that classification doesn't dominate latency (target: <100ms)
- Must run on vLLM (rules out bitsandbytes-only quantizations)
- Classification accuracy matters more than generation quality — it only needs to output one word

## Nemotron Family

All distilled/pruned from larger Nemotron models. Staying in-family keeps compatibility predictable with vLLM and NVIDIA tooling.

### nvidia/Nemotron-Mini-4B-Instruct (current)

- **Parameters:** 4B
- **Status:** Currently deployed as the router/classifier. Also handles SIMPLE queries directly.
- **VRAM:** ~8 GB fp16, ~4 GB with fp8 on-the-fly quantization (current config).
- **Strengths:** Fast classification (<100ms typical), fits comfortably in the 10% VRAM budget with room for KV cache, proven stable in production.
- **Weaknesses:** General-purpose instruct model, not purpose-built for routing. Classification accuracy is "good enough" but not exceptional — occasionally misroutes edge cases (visible in session logs). Response quality on SIMPLE queries is basic.
- **Link:** https://huggingface.co/nvidia/Nemotron-Mini-4B-Instruct

### nvidia/Nemotron-Orchestrator-8B

- **Parameters:** 8B
- **Why interesting:** Purpose-built for multi-model routing, task orchestration, and agentic workflows. This is literally designed for what we're doing — classifying queries and deciding which model handles them.
- **Concern:** At 8B, fp16 weights are ~16 GB. Would need fp8 quantization (~8 GB) to fit in the 10% VRAM budget. Tight but possible.
- **Link:** https://huggingface.co/nvidia/Nemotron-Orchestrator-8B

### nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B

- **Parameters:** 30B total, ~3B active (Mixture of Experts)
- **Why interesting:** MoE architecture means only 3B params are active per forward pass despite 30B total. Optimized for classification, summarization, and routing. This is the same architecture as our primary model but in BF16 — we could potentially use it as both router and primary if the active param count keeps latency low.
- **Concern:** 30B total weights still need to fit in memory even if only 3B are active. BF16 weights would be ~60 GB — far too large for the router budget. Would need aggressive quantization, and even then the total weight footprint may be prohibitive for a router role.
- **Link:** https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16

### nvidia/NVIDIA-Nemotron-Nano-9B-v2

- **Parameters:** 9B
- **Why interesting:** Strong reasoning and classification capabilities. A step up from Mini 4B in quality without the MoE complexity.
- **Concern:** Same sizing challenge as the Orchestrator 8B. fp16 ~18 GB, fp8 ~9 GB. Fits with fp8 but leaves minimal KV cache headroom in the 10% budget.
- **Link:** https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-9B-v2

### nvidia/Nemotron-Flash-3B

- **Parameters:** 3B (hybrid architecture)
- **Why interesting:** Ultra-low latency, designed for edge/on-device use. Smaller than our current 4B router, so it would be faster and use less VRAM. If classification accuracy is comparable to Mini 4B, this is a strict upgrade for the router role.
- **Concern:** 3B may sacrifice classification quality. Needs benchmarking against Mini 4B on our actual routing prompt.
- **Link:** https://huggingface.co/nvidia/Nemotron-Flash-3B

## Evaluation Criteria

When testing a candidate router model, measure against the current Mini 4B baseline:

1. **Classification accuracy** — Run the same queries through both models. Does the candidate agree with Mini 4B? Where it disagrees, which is correct? (Session logs are the test corpus.)
2. **Latency** — Classification must be fast. Current Mini 4B target is <100ms per classification. Anything slower adds noticeable delay to every request.
3. **VRAM footprint** — Must fit within the router budget (currently 10% = ~10 GB) including KV cache for the classification prompt.
4. **vLLM compatibility** — Must load and serve via vLLM without custom patches. Check quantization format support.

## VRAM Budget Math

Quick reference for estimating whether a model fits the router slot:

| Model size | fp16 weights | fp8 weights | Fits in ~10 GB? |
|-----------|-------------|------------|-----------------|
| 3B | ~6 GB | ~3 GB | Yes (plenty of KV cache room) |
| 4B (current) | ~8 GB | ~4 GB | Yes |
| 8B | ~16 GB | ~8 GB | Tight — ~2 GB for KV cache |
| 9B | ~18 GB | ~9 GB | Barely — ~1 GB for KV cache |
| 30B MoE | ~60 GB | ~30 GB | No |

The 8-9B models could fit with fp8 but leave very little room for KV cache. For a classification task (short prompts, 1-token output), that's probably fine. For anything requiring longer context windows, they'd need a larger VRAM allocation — which means stealing from the primary model.
