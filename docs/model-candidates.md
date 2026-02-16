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
- **Concern:** At 8B, fp16 weights are ~16 GB. fp8 on-the-fly (~8 GB) fits but is tight.
- **Link:** https://huggingface.co/nvidia/Nemotron-Orchestrator-8B
- **AWQ 4-bit variant (deployed):** [cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit](https://huggingface.co/cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit) — ~6 GB loaded (vLLM reports `Model loading took 6.0 GiB`), uses Marlin INT4 dequant kernels via `compressed-tensors` format. Currently deployed as the classifier at 14% VRAM budget. Community quant (cyankiwi), safetensors format. Base model is Qwen3-8B fine-tuned by NVIDIA. NVIDIA license (research/dev only, fine for homelab).
- **NVFP4 variant (untested):** [ericlewis/Nemotron-Orchestrator-8B-NVFP4](https://huggingface.co/ericlewis/Nemotron-Orchestrator-8B-NVFP4) — FP4 quantization that would use Blackwell's native FP4 tensor cores (hardware dequant vs AWQ's software Marlin kernels). Potentially lower classification latency. ~5 GB weights per HF metadata. Community quant (ericlewis). vLLM compatibility unconfirmed — HF page says "not deployed by any Inference Provider." Worth benchmarking against AWQ once classification accuracy baseline is established.

### nvidia/Nemotron-Flash-3B

- **Parameters:** 3B (hybrid architecture)
- **Why interesting:** Ultra-low latency, designed for edge/on-device use. Smaller than our current 4B router, so it would be faster and use less VRAM. If classification accuracy is comparable to Mini 4B, this is a strict upgrade for the router role.
- **Concern:** 3B may sacrifice classification quality. Needs benchmarking against Mini 4B on our actual routing prompt.
- **Link:** https://huggingface.co/nvidia/Nemotron-Flash-3B

## Migration Path: Classifier-Only Router

The current Mini 4B does double duty — it classifies queries *and* answers SIMPLE ones directly. A purpose-built routing model like the Orchestrator wouldn't be good at answering general questions.

The clean migration: **make the router classification-only.** Drop the SIMPLE route as a separate backend destination. SIMPLE and MODERATE both go to the primary model. The classification labels are preserved (the classifier still outputs SIMPLE vs MODERATE) but they route to the same backend.

This gives us:

| Classification | Route | Backend |
|----------------|-------|---------|
| SIMPLE | `primary` | Nano 30B (local) |
| MODERATE | `primary` | Nano 30B (local) |
| COMPLEX | `xai` | Grok (xAI API) |
| ENRICH | `xai` + `primary` | Grok → Nano 30B |
| META | `primary` | Nano 30B (local, bypasses classification) |

The router model becomes single-purpose: read query, output one word, done. All generation happens elsewhere. This simplifies the architecture and makes the router model swappable without affecting response quality.

Code changes required:
- `get_model_url()`: map `'router'` → `PRIMARY_URL` instead of `ROUTER_URL`
- Or simpler: change the classifier to output MODERATE instead of SIMPLE (merge the labels)
- Remove SIMPLE-specific handling if any exists

## Evaluation Criteria

When testing a candidate router model, measure against the current Mini 4B baseline:

1. **Classification accuracy** — Run the same queries through both models. Does the candidate agree with Mini 4B? Where it disagrees, which is correct? (Session logs are the test corpus.)
2. **Latency** — Classification must be fast. Current Mini 4B target is <100ms per classification. Anything slower adds noticeable delay to every request.
3. **VRAM footprint** — Must fit within the router budget (currently 10% = ~10 GB) including KV cache for the classification prompt.
4. **vLLM compatibility** — Must load and serve via vLLM without custom patches. Check quantization format support.

## VRAM Budget Math

Quick reference for estimating whether a model fits the router slot:

| Model size | fp16 weights | fp8 weights | AWQ 4-bit | Fits in ~10 GB? |
|-----------|-------------|------------|-----------|-----------------|
| 3B | ~6 GB | ~3 GB | ~1.5 GB | Yes (plenty of KV cache room) |
| 4B (current) | ~8 GB | ~4 GB | — | Yes |
| 8B | ~16 GB | ~8 GB | ~4 GB | fp8: tight. AWQ 4-bit: yes, comfortably |

The AWQ 4-bit Orchestrator variant is the most promising upgrade path — same VRAM footprint as the current Mini 4B at fp8, but purpose-built for routing. Models 9B+ are excluded — they don't fit within the 10% VRAM budget at any quantization.
