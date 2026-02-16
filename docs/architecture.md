# AI Router - Architecture Diagram

## System Overview

Three-tier intelligent routing system that classifies queries by complexity and routes them to the optimal AI model, with an enrichment pipeline for real-time information.

## Pipeline Diagram

```mermaid
flowchart TD
    Client([Client Request])

    subgraph Traefik["Traefik Reverse Proxy :80"]
        LB[Path-based Routing]
    end

    subgraph Router Service["AI Router :8002 — Flask"]
        Parse[Parse JSON & Validate Messages]
        Classify[Classify Query via Mini 4B]
        Decision{Route Decision}
        Enrich[Fetch Enrichment Context]
        Inject[Inject Context as System Message]
        Forward[Forward Request to Backend]
    end

    subgraph Backends
        direction TB
        Mini["<b>Router Model</b><br/>Nemotron Mini 4B<br/>vllm-router :8001<br/><i>8G VRAM</i>"]
        Primary["<b>Primary Model</b><br/>Nemotron Nano 30B<br/>vllm-primary :8000<br/><i>24G VRAM</i>"]
        XAI["<b>xAI API</b><br/>grok-4-1-fast-reasoning<br/>api.x.ai<br/><i>External</i>"]
    end

    Response([Response to Client])

    Client --> LB
    LB -->|"/v1/chat/completions"| Parse
    Parse --> Classify
    Classify -->|"POST /v1/chat/completions<br/>max_tokens=10, temp=0"| Mini
    Mini -->|"SIMPLE / MODERATE / COMPLEX / ENRICH"| Decision

    Decision -->|"SIMPLE"| Forward
    Decision -->|"MODERATE"| Forward
    Decision -->|"COMPLEX"| Forward
    Decision -->|"ENRICH"| Enrich

    Enrich -->|"Query xAI for<br/>real-time context"| XAI
    XAI -->|"Factual context"| Inject
    Inject --> Forward

    Forward -->|"SIMPLE"| Mini
    Forward -->|"MODERATE"| Primary
    Forward -->|"COMPLEX"| XAI
    Forward -->|"ENRICH<br/>(enriched messages)"| Primary

    Mini --> Response
    Primary --> Response
    XAI --> Response
    Response --> Client

    style Traefik fill:#2d3748,color:#e2e8f0
    style Mini fill:#4a6741,color:#fff
    style Primary fill:#4a6741,color:#fff
    style XAI fill:#6b4c8a,color:#fff
    style Decision fill:#b7791f,color:#fff
    style Enrich fill:#6b4c8a,color:#fff
    style Inject fill:#6b4c8a,color:#fff
```

## Enrichment Pipeline Detail

```mermaid
flowchart LR
    Query[User Query<br/><i>requires current info</i>]
    XAI_Enrich["xAI API<br/><b>enrichment-system-prompt.md</b><br/>max_tokens=1024"]
    Template["Wrap in<br/><b>enrichment-injection-prompt.md</b>"]
    Prepend["Prepend as<br/>system message"]
    Primary["Primary Model<br/>Nemotron Nano 30B"]

    Query --> XAI_Enrich
    XAI_Enrich -->|"Concise factual context<br/>with dates & timeframes"| Template
    Template --> Prepend
    Prepend -->|"Enriched messages[]"| Primary

    style XAI_Enrich fill:#6b4c8a,color:#fff
    style Primary fill:#4a6741,color:#fff
```

## Routing Classification

```mermaid
flowchart LR
    subgraph Classification["routing-prompt.md"]
        direction TB
        S["<b>SIMPLE</b><br/>Greetings, casual chat,<br/>basic factual questions"]
        M["<b>MODERATE</b><br/>Explanations, coding help,<br/>standard analysis"]
        C["<b>COMPLEX</b><br/>Research-level, novel problems,<br/>cutting-edge topics"]
        E["<b>ENRICH</b><br/>Current events, real-time data,<br/>post-training-cutoff info"]
    end

    S --> Mini["Mini 4B"]
    M --> Primary["Nano 30B"]
    C --> XAI["xAI Grok"]
    E --> Enrichment["Enrichment Pipeline<br/>then Nano 30B"]

    style S fill:#38a169,color:#fff
    style M fill:#d69e2e,color:#fff
    style C fill:#e53e3e,color:#fff
    style E fill:#6b4c8a,color:#fff
```

## Deployment Topology

```mermaid
flowchart TD
    subgraph Docker["Docker Compose — ai-network bridge"]
        Traefik["<b>Traefik v3.6</b><br/>:80 HTTP / :8080 Dashboard"]
        App["<b>ai-router</b><br/>python:3.12-slim<br/>Flask :8002"]
        VLLM_R["<b>vllm-router</b><br/>vllm/vllm-openai<br/>:8001 — GPU device 0"]
        VLLM_P["<b>vllm-primary</b><br/>vllm/vllm-openai<br/>:8000 — GPU device 0"]
        Cache[("hf-cache<br/>volume")]
    end

    ExtXAI["<b>xAI API</b><br/>https://api.x.ai"]

    Traefik --> App
    App --> VLLM_R
    App --> VLLM_P
    App --> ExtXAI
    VLLM_R --> Cache
    VLLM_P --> Cache

    style Docker fill:#1a202c,color:#e2e8f0
    style ExtXAI fill:#6b4c8a,color:#fff
    style Cache fill:#2c5282,color:#fff
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Root — API info |
| `/health` | GET | Aggregated health check (router + primary + optional xAI) |
| `/v1/chat/completions` | POST | Main chat endpoint with auto-routing |
| `/v1/completions` | POST | Legacy completions passthrough |
| `/v1/models` | GET | List models from all backends |
| `/api/route` | POST | Explicit routing control for testing |
| `/stats` | GET | Routing statistics |
