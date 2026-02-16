# Inspiration & Prior Art

This project isn't novel — intelligent LLM routing is an active area of research and commercial development. What's less common is the specific combination: local-first routing on consumer/prosumer hardware, a two-hop enrichment pipeline, and a closed-loop improvement system driven by session logs.

This document maps the landscape of related projects and where they overlap with (or diverge from) what we're building.

## Routing & Classification

### RouteLLM (lm-sys)

The closest academic analog. Trained classifier models that route between strong/weak LLMs based on query complexity. They published cost-quality tradeoff research showing that a small router model can dramatically reduce costs while maintaining quality.

Our prompt-based classification with Mini 4B is the lightweight version of what they do with fine-tuned routers. If we ever move beyond prompt-based classification, their methodology for training a router on preference data is the playbook.

- Repository: https://github.com/lm-sys/RouteLLM
- Key idea: Train a router on human preference data (Chatbot Arena) to predict when the strong model is needed.

### Martian Model Router

Commercial router that picks the best model per-request based on quality, cost, and latency targets. Their framing of "which model is cheapest for this quality target" maps directly to our SIMPLE/MODERATE/COMPLEX tiers.

- Key idea: Route as a cost-quality optimization problem, not just a complexity classifier.

### Not Diamond

Another model router with a classifier that predicts which model will perform best on a given query. Their benchmark methodology for evaluating router accuracy is worth studying — specifically how they measure "did the router pick the right model" against ground truth.

- Key idea: Per-query model selection based on predicted performance, with evaluation benchmarks.

## API Gateway & Proxy

### LiteLLM

OpenAI-compatible proxy for 100+ LLM providers. Not a router (it doesn't pick models for you), but the most mature example of the "unified API in front of heterogeneous backends" pattern we use. Their fallback, retry, and rate-limiting logic is worth studying for hardening `forward_request()`.

- Repository: https://github.com/BerriAI/litellm
- Key idea: Unified OpenAI-compatible interface with provider abstraction, fallbacks, and observability.

### Portkey AI Gateway

Similar to LiteLLM but adds semantic caching, automatic retries, and load balancing. Their caching layer is interesting — if the same question comes in twice, skip inference entirely. Relevant for when we think about reducing redundant xAI calls.

- Repository: https://github.com/Portkey-ai/gateway
- Key idea: Semantic caching and automatic fallback chains across providers.

### OpenRouter

Commercial multi-model gateway. Less relevant for code, more for how they present a unified model list while routing underneath. Their pricing model (pay-per-token across providers) is also a useful mental model for thinking about cost optimization even in a self-hosted context (local = free tokens, xAI = paid tokens).

- Key idea: Transparent multi-provider routing behind a single API.

## Enrichment & RAG

### Perplexity (architecture, not open source)

Their "search then synthesize" pipeline is essentially our ENRICH route: web search first, then LLM generation with injected context. Well-documented in blog posts and interviews. The key insight we share: separating "information retrieval" from "response generation" into explicit, observable steps.

- Key idea: Two-hop pipeline — retrieve current information, then generate a grounded response.

### LangChain / LlamaIndex

Overkill frameworks for our use case, but their retrieval-augmented generation patterns document the same two-hop "fetch context → inject → generate" pipeline we built. Useful as reference for injection prompt design and context window management.

- Key idea: Standardized patterns for context injection and retrieval-augmented generation.

## Self-Improvement & Prompt Optimization

### DSPy (Stanford)

The closest thing to our "autonomous agent reviews logs and improves prompts" vision. DSPy programmatically optimizes prompt templates based on evaluation metrics. Different execution model (compile-time optimization vs our runtime log analysis), but the goal of automated prompt refinement from observed outcomes is the same.

If we build the autonomous agent that consumes session logs, DSPy's optimization strategies (bootstrap few-shot, MIPRO, etc.) are directly applicable to improving our `config/prompts/` templates.

- Repository: https://github.com/stanfordnlp/dspy
- Key idea: Treat prompts as programs with tunable parameters; optimize them against metrics automatically.

### TextGrad

Uses LLMs to provide "gradients" (natural language feedback) on text outputs, then iteratively improves prompts. Academic but aligns with our closed-loop improvement idea — an LLM reviewing session logs and suggesting prompt changes is essentially TextGrad applied to a production system.

- Repository: https://github.com/zou-group/textgrad
- Key idea: LLM-as-optimizer — use model feedback to iteratively refine prompts.

## Where We Differ

Most of these projects assume **cloud-to-cloud routing** — choosing between GPT-4 and Claude and Gemini, all behind APIs. Our project routes between **local models on constrained hardware and a cloud fallback**. This changes the problem in a few ways:

1. **The quality boundary is hardware-specific.** What a 4B model can handle on our GPU is different from what it could handle with more VRAM or a different quantization. Session logs are how we discover that boundary empirically rather than assuming it.

2. **Latency tradeoffs are inverted.** In cloud routing, the strong model is slower because it's bigger. In our setup, the local models have zero network latency but limited capability, while the cloud model (xAI) adds network round-trips but is much more capable. Classification overhead matters more when local inference is fast.

3. **Cost is binary, not granular.** Cloud routers optimize across a spectrum of per-token prices. We have two cost tiers: free (local) and not-free (xAI). The optimization goal is simpler — minimize xAI calls without degrading quality — but the stakes per-decision are higher.

4. **Enrichment is a first-class route, not a plugin.** Most routers treat RAG/search as an external concern. We bake it into the routing decision itself — the classifier can say "this needs current information" and trigger a two-hop pipeline. The enrichment step is visible in session logs alongside routing decisions.

5. **Self-improvement from production logs.** Most routing projects are static — train a router, deploy it, done. Our vision is a closed loop where session logs from real usage feed back into prompt refinement. This is closer to DSPy's philosophy but applied at the system level rather than the prompt level.

6. **PII as a routing dimension.** The local/cloud boundary isn't just a complexity boundary — it's a privacy boundary. Data routed to SIMPLE, MODERATE, or META never leaves the local machine. Data routed to COMPLEX or ENRICH hits xAI's API. This makes the routing decision a de facto PII gate. Future work: teach the classifier (or add a separate detection step) to recognize PII-heavy queries and force them local, even if complexity would normally warrant cloud escalation. The tradeoff is a potentially weaker local answer vs. sensitive data leaving the network. More advanced: scrub PII before cloud forwarding, then re-inject originals into the response. Session logs already capture what gets sent to cloud, making PII leakage auditable today.

## The Hardware Reality

This project runs on "hal" — a high-end prosumer workstation:

| Component | Cost (approx) |
|-----------|---------------|
| RTX PRO 6000 Blackwell (96 GB VRAM) | ~$8,000 |
| System (Ryzen 9 9950X3D, 92 GB DDR5, NVMe) | ~$2,000 |
| Additional 96 GB DDR5 kit (uninstalled) | ~$500–800 |
| **Total invested** | **~$10,500+** |

Even at this price point, VRAM is the binding constraint. The 96 GB GPU can hold a quantized 30B model plus a 4B classifier with ~10% headroom — but a single 70B model at fp16 wouldn't fit, and anything larger is out of the question. The extra RAM sitting in a box won't help with inference; VRAM is what matters for LLM workloads.

This is the fundamental motivation for the router: $10k of hardware gets you *most* of the way to a capable local AI, but the long tail of complex queries still needs cloud. The routing layer bridges that gap for a few dollars a month in API costs. The ROI isn't "replace cloud entirely" — it's "keep 80-90% of queries local and free, escalate the rest cheaply."

Most open-source routing projects assume you're choosing between cloud providers at different price points. We're solving a different problem: maximizing the utility of expensive local hardware while accepting that it has a ceiling.
