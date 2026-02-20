# Router Model Candidates

The current router/classifier is **Nemotron Orchestrator 8B AWQ** ([cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit](https://huggingface.co/cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit)) — purpose-built for query routing. This document tracks the evaluation history and alternative models.

The constraints for any router model candidate:
- Must fit within ~10% of 96 GB VRAM (~10 GB budget) alongside the primary model
- Must respond fast enough that classification doesn't dominate latency (target: <100ms)
- Must run on vLLM (rules out bitsandbytes-only quantizations)
- Classification accuracy matters more than generation quality — it only needs to output one word

## Nemotron Family

All distilled/pruned from larger Nemotron models. Staying in-family keeps compatibility predictable with vLLM and NVIDIA tooling.

### nvidia/Nemotron-Mini-4B-Instruct (previous)

- **Parameters:** 4B
- **Status:** Replaced by Orchestrator 8B AWQ. Was the original router/classifier and also handled simple queries directly.
- **VRAM:** ~8 GB fp16, ~4 GB with fp8 on-the-fly quantization.
- **Strengths:** Fast classification (<100ms typical), fits comfortably in the 10% VRAM budget with room for KV cache, proven stable in production.
- **Weaknesses:** General-purpose instruct model, not purpose-built for routing. Classification accuracy was "good enough" but not exceptional — occasionally misrouted edge cases.
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
- **Why interesting:** Ultra-low latency, designed for edge/on-device use. Smaller than the Orchestrator 8B, so it would be faster and use less VRAM. If classification accuracy is comparable, it could reduce the ~1–3.8s classification latency that currently dominates primary routes.
- **Concern:** 3B may sacrifice classification quality. Needs benchmarking against Orchestrator 8B on our actual routing prompt.
- **Link:** https://huggingface.co/nvidia/Nemotron-Flash-3B

## Migration Path: Classifier-Only Router

**Status: completed.** The router is now classification-only. The old Mini 4B used to do double duty (classify queries *and* answer SIMPLE ones directly). With the Orchestrator 8B deployed, the SIMPLE label was merged into MODERATE — the classifier outputs three labels (MODERATE, COMPLEX, ENRICH) and all local queries route to the primary model.

| Classification | Route | Backend |
|----------------|-------|---------|
| MODERATE | `primary` | Nano 30B (local) |
| COMPLEX | `xai` | Grok (xAI API) |
| ENRICH | `xai` + `primary` | Grok → Nano 30B |
| META | `primary` | Nano 30B (local, bypasses classification) |

The router model is single-purpose: read query, output one word, done. All generation happens elsewhere. This simplifies the architecture and makes the router model swappable without affecting response quality.

## Evaluation Criteria

When testing a candidate router model, measure against the current Orchestrator 8B AWQ baseline:

1. **Classification accuracy** — Run the same queries through both models. Does the candidate agree with Orchestrator 8B? Where it disagrees, which is correct? (Session logs are the test corpus.)
2. **Latency** — Classification must be fast. Current Orchestrator 8B takes ~1–3.8s due to `<think>` reasoning. A non-reasoning model could cut this to <100ms.
3. **VRAM footprint** — Must fit within the router budget (currently 14% = ~13 GB) including KV cache for the classification prompt.
4. **vLLM compatibility** — Must load and serve via vLLM without custom patches. Check quantization format support.

## VRAM Budget Math

Quick reference for estimating whether a model fits the router slot:

| Model size | fp16 weights | fp8 weights | AWQ 4-bit | Fits in ~10 GB? |
|-----------|-------------|------------|-----------|-----------------|
| 3B | ~6 GB | ~3 GB | ~1.5 GB | Yes (plenty of KV cache room) |
| 4B (previous) | ~8 GB | ~4 GB | — | Yes |
| 8B (current) | ~16 GB | ~8 GB | ~4 GB | fp8: tight. AWQ 4-bit: yes, comfortably |

The Orchestrator 8B AWQ 4-bit is the current deployed model — ~6 GB weights, fitting well within the 14% VRAM budget. Models 9B+ are excluded — they don't fit at any quantization.
