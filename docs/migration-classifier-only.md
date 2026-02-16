# Migration Plan: Classifier-Only Router

**Goal:** Make the router model a pure classifier that never answers queries directly. All generation moves to the primary model. This decouples classification quality from generation quality and enables swapping to purpose-built routing models (e.g., Orchestrator 8B AWQ) without affecting response quality.

**Current state:** The Mini 4B router does double duty — classifies queries AND answers SIMPLE ones. The `router` route sends generation requests back to the router model.

**Target state:** The router model only classifies. SIMPLE and MODERATE both route to the primary model. The `router` route as a generation destination is eliminated.

---

## Changes Required

### 1. `src/providers.py` — Route mapping

**`get_model_url()`** (line ~276): Remove the `router` branch. SIMPLE now routes to primary.

```python
# BEFORE
def get_model_url(route: str) -> str:
    if route == 'router':
        return ROUTER_URL
    elif route == 'xai':
        return XAI_API_URL
    else:
        return PRIMARY_URL

# AFTER
def get_model_url(route: str) -> str:
    if route == 'xai':
        return XAI_API_URL
    else:
        return PRIMARY_URL
```

**`determine_route()`** (line ~159): Change the SIMPLE handler to return `'primary'` instead of `'router'`.

```python
# BEFORE
elif 'SIMPLE' in decision:
    logger.info("Routing to router model: prompt-based classification (SIMPLE)")
    route = 'router'

# AFTER
elif 'SIMPLE' in decision:
    logger.info("Routing to primary model: prompt-based classification (SIMPLE)")
    route = 'primary'
```

**Docstring** (line ~26): Update the routes docstring to reflect SIMPLE → primary.

**`forward_request()`** (line ~320): The `route == 'router'` branch that sets `data['model'] = ROUTER_MODEL` is now dead code. Remove it — all non-xAI requests use `PRIMARY_MODEL`.

```python
# BEFORE
if route == 'xai' and XAI_API_KEY:
    headers['Authorization'] = f'Bearer {XAI_API_KEY}'
    data['model'] = XAI_MODEL
elif route == 'router':
    data['model'] = ROUTER_MODEL
else:
    data['model'] = PRIMARY_MODEL

# AFTER
if route == 'xai' and XAI_API_KEY:
    headers['Authorization'] = f'Bearer {XAI_API_KEY}'
    data['model'] = XAI_MODEL
else:
    data['model'] = PRIMARY_MODEL
```

### 2. `src/app.py` — Explicit route endpoint

**`api_route()`** (line ~210): Remove `'router'` from the allowed routes list.

```python
# BEFORE
elif route not in ['router', 'primary', 'xai', 'enrich']:
    return jsonify({
        'error': 'Invalid route',
        'message': 'Route must be "router", "primary", "xai", "enrich", or "auto"'
    }), 400

# AFTER
elif route not in ['primary', 'xai', 'enrich']:
    return jsonify({
        'error': 'Invalid route',
        'message': 'Route must be "primary", "xai", "enrich", or "auto"'
    }), 400
```

**`stats()`** (line ~236): Update the route descriptions stub.

```python
# BEFORE
'routes': {
    'router': 'Fast model for simple queries',
    'primary': 'Powerful model for complex reasoning'
}

# AFTER
'routes': {
    'primary': 'Local model for simple and moderate queries',
    'xai': 'Cloud model for complex queries and enrichment'
}
```

### 3. `src/config.py` — No code changes

`ROUTER_URL` and `ROUTER_MODEL` remain — they're still used for the classification call in `determine_route()`. The router model still serves, it just only does classification now.

### 4. `config/prompts/routing/request.md` — No changes

Keep the SIMPLE label. The classifier still outputs SIMPLE vs MODERATE — we just route them both to the same backend. Preserving the distinction is useful for session log analysis (tracking what fraction of queries are trivial).

### 5. `config/prompts/routing/system.md` — No changes

Same reasoning as above.

### 6. `CLAUDE.md` — Update routing table

```markdown
<!-- BEFORE -->
| SIMPLE → `router` | Nemotron Mini 4B (local) | Greetings, trivial questions |

<!-- AFTER -->
| SIMPLE → `primary` | Nemotron Nano 30B (local) | Greetings, trivial questions |
```

Also update:
- Models table: Router description changes from "Also handles SIMPLE queries directly" to "Classification only"
- Architecture decisions: Note that the router is classifier-only
- Misclassifications bullet: Update wording since SIMPLE now goes to primary

### 7. `docs/architecture.md` — Update Mermaid diagrams

**Pipeline diagram:**
- Remove the `Forward -->|"SIMPLE"| Mini` edge
- SIMPLE should route to `Primary` instead
- The `Mini` node should only receive classification requests, not generation

**Classification diagram:**
- `S --> Mini["Mini 4B"]` changes to `S --> Primary["Nano 30B"]`

**Route table in the doc** (if present): Update SIMPLE row.

### 8. `Test` script — Update integration tests

- **Section testing SIMPLE routing** (~line 113-139): Update expected behavior. SIMPLE queries should now return responses from the primary model, not the router model. The test that checks "Correctly routed to router model (Mini 4B)" needs to check for primary model instead.
- **Direct router access tests** (~line 314-335): These test hitting `$BASE_URL/router/v1/chat/completions` directly. Keep these — Traefik still routes to the vLLM router container. But add a note that this bypasses the AI Router and hits vLLM directly (useful for debugging classification, not for generation).
- **Explicit route API test** (~line 372): Remove `"route": "router"` test or change to `"route": "primary"`.
- **Summary section** (~line 409): Update `SIMPLE → Mini 4B (router model)` to `SIMPLE → Nano 30B (primary model)`.

### 9. `Benchmark` script — Update references

- **SIMPLE query label** (~line 45): Change "Mini 4B via router" description. The routed request now goes to primary.
- **Direct router throughput test** (~line 123-132): Keep — still useful for measuring raw classifier model performance.
- **Concurrent router tests** (~line 185-188): Keep — still useful for stress-testing classification.
- **Model info section** (~line 266-267): Update SIMPLE description from "Classification + Simple queries" to "Classification only".
- **Summary** (~line 299): Update `SIMPLE → Mini 4B: Fast classification + simple queries` to `SIMPLE → Nano 30B: Simple queries (classified by Mini 4B)`.

### 10. `docker-compose.yml` — No changes

The vLLM router container stays. It still serves classification requests. No model or VRAM changes needed for this migration.

---

## What NOT to change

- **Classification labels** — Keep SIMPLE/MODERATE/COMPLEX/ENRICH. The labels are still meaningful for analytics.
- **Session log format** — `classification_raw` still records SIMPLE. The `route` field will now show `primary` instead of `router` for SIMPLE queries.
- **Router vLLM container** — Stays running, still serves classification. Just no longer receives generation requests.
- **`ROUTER_URL` / `ROUTER_MODEL` config** — Still needed for the classification call in `determine_route()`.

---

## Verification

After applying changes:

1. **Classification still works:** Send a SIMPLE query ("Hello"), verify session log shows `classification_raw: "SIMPLE"` and `route: "primary"`.
2. **Response quality improves for SIMPLE:** The Nano 30B should give better answers to trivial questions than the Mini 4B did.
3. **No regression on other routes:** MODERATE, COMPLEX, ENRICH, META all behave identically.
4. **`/api/route` rejects `"router"`:** Confirm 400 response.
5. **Session logs:** Verify no session logs show `route: "router"` after migration.
6. **Run `make test`:** All integration tests pass with updated expectations.
7. **Run `make benchmark`:** Latency for SIMPLE queries will be slightly higher (Nano 30B vs Mini 4B) but response quality better.

---

## Future: Orchestrator Swap

Once classifier-only is stable, swapping the router model to the Orchestrator 8B AWQ is a separate, smaller change:

1. `docker-compose.yml`: Change `--model` to `cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit`, remove `--quantization fp8`
2. `.env` / `ROUTER_MODEL`: Update to match the new model name
3. `config/prompts/routing/`: May need prompt adjustments for the Orchestrator's preferred instruction format
4. Test classification accuracy against session log corpus from Mini 4B era
