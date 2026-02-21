# Performance Plan: Request Processing & User Experience

**Goal:** Reduce user-perceived latency — especially time-to-first-token (TTFT) — by addressing bottlenecks identified through benchmarking, session log analysis, and application-level timing logs. No architectural changes; these are targeted optimizations to the existing pipeline.

---

## Baseline Benchmarks

**Date:** 2026-02-16 (three runs — initial, post-logging, post-logging-v2)

### Per-Route Latency (Benchmark 1)

Measured end-to-end from client `curl` to response received. Classification, enrichment, and inference durations come from the `REQUEST` summary lines in `app.log`.

| Route | Classification | Enrichment | Inference | Total | Classification % |
|-------|---------------|------------|-----------|-------|-----------------|
| MODERATE (short) | 952–1,087ms | — | 208–396ms | 1,181–1,426ms | **67–81%** |
| MODERATE (long) | 1,394–1,806ms | — | 402–2,204ms | 1,803–3,873ms | **43–77%** |
| COMPLEX | 1,434–1,477ms | — | 7,347–11,007ms | 8,825–12,442ms | 12–17% |
| ENRICH | 855–1,426ms | 11,127–25,987ms | 1,415–3,498ms | 13,845–30,913ms | 3–6% |
| META | 0ms (skipped) | — | 1,006–1,250ms | 1,007–1,250ms | 0% |

### Routing Overhead (Benchmark 6)

| Metric | Run 2 | Run 3 |
|--------|-------|-------|
| Direct vLLM call | 33ms | 36ms |
| Routed call (via ai-router) | 1,318ms | 1,432ms |
| **Routing overhead** | **1,284ms** | **1,396ms** |

### Throughput & Streaming (Benchmarks 2, 5)

| Metric | Value |
|--------|-------|
| Router model throughput | 192–193 tok/s |
| Primary model throughput | 233–234 tok/s |
| Streaming TTFT (direct to vLLM) | 77–89ms |
| Long context (8K) | **FAILED** (see item 6) |
| Concurrent requests (10x router) | 10/10 in 135–163ms |

### Streaming TTFT Through Router (from `Provider response` log)

| Metric | Value |
|--------|-------|
| connect_ms (HTTP headers) | 17ms |
| ttft_ms (first SSE chunk) | 48ms |
| Total TTFT (including ~1s classification) | ~1,050ms |

The 48ms provider TTFT is close to the 77–89ms direct-to-vLLM benchmark. The classification overhead (~1s) is the real TTFT penalty for streamed requests — identical to the non-streaming case. Speculative execution (item 1) would eliminate this for primary routes.

### Health Check Timing (from `Health check` log lines)

| Metric | Value |
|--------|-------|
| Healthy path (3 sequential calls) | 114–149ms |
| Worst case (one backend down) | up to 5.1s per timeout |
| Worst case (all down) | up to 15s |

The healthy path is fast because all three backends respond quickly on the local Docker network. The parallel optimization (item 3) only matters when backends are slow or unreachable — but that's exactly when health checks block a Werkzeug thread.

### GPU Utilization

| Metric | Value |
|--------|-------|
| VRAM used | 86,447 MB (88.3%) |
| VRAM free | 10,804 MB |
| Configured budget | 79% (14% router + 65% primary) |
| Actual overhead | ~10% CUDA context |

### Session I/O (from `Session saved` log lines)

| Metric | Value |
|--------|-------|
| JSON write time | 0ms (< 1ms, rounds down) |
| Cleanup time | 0ms (< 1ms, low file count) |

Cleanup time will increase as session files approach the 5,000 max. Currently negligible — revisit if `cleanup_ms` starts appearing in logs.

---

## Key Findings

### 1. Classification dominates primary routes

The Orchestrator 8B takes 950–1,800ms to reason through its `<think>` block before emitting a one-word classification. For short MODERATE queries (inference ~200–400ms), classification is **3–4× longer** than the actual response. For longer MODERATE queries, classification is 43–77% of total time depending on response length.

~80% of real-world requests route to primary (MODERATE). Every one of them pays the full classification tax before inference can start. This applies equally to streaming — the user sees no tokens until classification completes, adding ~1s to perceived TTFT.

### 2. Enrichment step dominates the ENRICH route (with high variance)

Across runs: classification 855–1,426ms (3–6%), xAI context fetch **11,127–25,987ms (72–84%)**, primary inference 1,415–3,498ms (10–13%). The xAI web search call is the bottleneck by far, with high variance (11s to 26s across runs) driven by search tool latency on the xAI side. This is inherent to the two-hop design.

### 3. Session I/O is not a bottleneck (yet)

Both write and cleanup consistently round to 0ms at current file counts. The plan to defer cleanup (item 2 below) is preventive — it'll matter when approaching 5,000 files where `glob.glob()` + `sorted()` over the session directory becomes measurable.

### 4. Meta pipeline is fast

Meta requests skip classification entirely (0ms) and go straight to primary inference (~1,000–1,250ms). This confirms the fast-path heuristic is working.

### 5. Health checks are fast when healthy, slow when degraded

At 114–149ms in the happy path, the sequential health check isn't a performance issue during normal operation. The problem is the degenerate case: if one backend is down, the 5s timeout blocks the Werkzeug thread before checking the remaining backends.

---

## Completed: Application Logging for Bottleneck Visibility

**Status:** Implemented and deployed.

Added structured timing logs at every stage of the pipeline. These run at every request and write to `app.log` alongside session JSONs, making bottleneck patterns grep-able without parsing individual JSON files.

### Log lines added

| Log pattern | Where | What it shows |
|-------------|-------|---------------|
| `Incoming request: messages={n} total_chars={n} stream={bool}` | `app.py` | Request context size for latency correlation |
| `Classification completed: {DECISION} -> {route} in {ms}ms` | `providers.py` | Per-request classifier duration and result |
| `Enrichment context retrieved: {chars} chars in {ms}ms` | `providers.py` | xAI context fetch duration |
| `Provider response: {route} ... duration_ms={ms} stream=false` | `providers.py` | Backend inference duration (non-streaming) |
| `Provider response: {route} ... connect_ms={ms} ttft_ms={ms} stream=true` | `providers.py` | Streaming connection time and time-to-first-token |
| `Session saved: {id} write_ms={ms} cleanup_ms={ms}` | `session_logger.py` | Disk I/O timing for session file write + cleanup |
| `REQUEST session={id} route= classification_ms= enrichment_ms= inference_ms= total_ms= stream=` | `app.py` | Per-request summary with full timing breakdown |
| `SLOW_REQUEST session={id} route= total_ms= ...` | `app.py` | Warning when request exceeds per-route threshold |
| `Health check: status={status} duration_ms={ms}` | `app.py` | Health endpoint total duration |

### Slow request thresholds

| Route | Threshold |
|-------|-----------|
| primary | 5,000ms |
| meta | 5,000ms |
| xai | 30,000ms |
| enrich | 60,000ms |

### Example: non-streaming primary request

```
Incoming request: messages=1 total_chars=5 stream=False
Classification completed: MODERATE -> primary in 973ms (finish_reason=stop)
Provider response: primary status=200 duration_ms=208 finish_reason=stop stream=false
REQUEST session=dba46d38 route=primary classification_ms=973 inference_ms=208 total_ms=1181 stream=False
Session saved: dba46d38 write_ms=0 cleanup_ms=0
```

### Example: streaming primary request

```
Incoming request: messages=1 total_chars=9 stream=True
Classification completed: MODERATE -> primary in 1014ms (finish_reason=stop)
REQUEST session=ed341480 route=primary classification_ms=1014 inference_ms=17 total_ms=1032 stream=True
Provider response: primary status=200 connect_ms=17 ttft_ms=48 stream=true
Session saved: ed341480 write_ms=0 cleanup_ms=0
```

Note: For streaming, the `REQUEST` line fires before token delivery begins (session is saved at response start), so `inference_ms` reflects only connect time. The `Provider response` line with `ttft_ms` fires when the first SSE chunk passes through the iterator.

### Example: enrichment request

```
Incoming request: messages=1 total_chars=60 stream=False
Classification completed: ENRICH -> enrich in 1426ms (finish_reason=stop)
Enrichment context retrieved: 2926 chars in 25987ms
Provider response: primary status=200 duration_ms=3498 finish_reason=stop stream=false
REQUEST session=80c4e473 route=enrich classification_ms=1426 enrichment_ms=25987 inference_ms=3498 total_ms=30913 stream=False
Session saved: 80c4e473 write_ms=0 cleanup_ms=0
```

---

## Changes Required

### 1. Speculative Primary Execution

**Problem:** All non-meta requests block on classification (~1.0–1.8s) before inference starts. For the ~80% of requests that route to primary, this is pure overhead. For streaming, this means ~1s of dead time before any tokens reach the user.

**Solution:** Fire the primary model request in parallel with classification. When classification returns:
- **MODERATE (most common):** The primary response is already in flight. TTFT drops by ~1.0–1.8s.
- **COMPLEX:** Cancel the speculative primary request, forward to xAI. Cost: one wasted local inference start (~200–400ms GPU time), no user-visible penalty since xAI starts immediately after classification.
- **ENRICH:** Cancel speculative request, run enrichment pipeline. Same trade-off.

**Implementation:**
- `src/app.py` — In `chat_completions()`, use `concurrent.futures.ThreadPoolExecutor` to run `determine_route()` and a speculative primary `forward_request()` in parallel.
- The speculative request uses a `requests.Session` so we can call `response.close()` to abort if the route isn't primary.
- For streaming: the speculative thread starts the SSE connection; if classification confirms primary, pipe chunks to the client. If not, close the connection.
- For non-streaming: wait for both futures; if route is primary, return the speculative result. Otherwise discard and forward to the correct backend.

**Risk:** Adds ~200–400ms of wasted GPU work per COMPLEX/ENRICH request. At single-user homelab load with 8 concurrent sequence slots (`--max-num-seqs 8`), one speculative request won't block real work.

**Files:** `src/app.py` (main), minor helper in `src/providers.py`

### 2. Defer Session Log Cleanup

**Problem:** `_cleanup()` in `session_logger.py` runs `glob.glob()` + `sorted()` over up to 5,000 files on every `save()` call. Currently 0ms at low file counts, but will degrade as files accumulate.

**Solution:** Run cleanup periodically — every 100 saves or every 60 seconds, whichever comes first. Track a class-level counter and timestamp.

```python
# BEFORE (session_logger.py — current)
def save(self):
    # ... write file ...
    self._cleanup()  # every request

# AFTER
_save_count = 0
_last_cleanup = time.time()
CLEANUP_INTERVAL = 100      # saves
CLEANUP_PERIOD = 60         # seconds

def save(self):
    # ... write file ...
    SessionLogger._save_count += 1
    if (SessionLogger._save_count >= CLEANUP_INTERVAL
            or time.time() - SessionLogger._last_cleanup > CLEANUP_PERIOD):
        self._cleanup()
        SessionLogger._save_count = 0
        SessionLogger._last_cleanup = time.time()
```

**Priority:** Low — not a bottleneck until file count is high. Monitor `cleanup_ms` in logs.

**Files:** `src/session_logger.py`

### 3. Parallel Health Checks

**Problem:** `/health` makes 3 sequential HTTP calls with 5s timeouts. Happy path is 114–149ms, but worst case (backend down) stacks to 15s, blocking a Werkzeug thread.

**Solution:** Use `concurrent.futures.ThreadPoolExecutor` to check all backends simultaneously. Worst case drops from 15s to 5s.

```python
from concurrent.futures import ThreadPoolExecutor

def _check(url, headers=None):
    try:
        return requests.get(url, headers=headers, timeout=5).status_code == 200
    except:
        return False

with ThreadPoolExecutor(max_workers=3) as pool:
    router_future = pool.submit(_check, f"{ROUTER_URL}/health")
    primary_future = pool.submit(_check, f"{PRIMARY_URL}/health")
    xai_future = pool.submit(_check, f"{XAI_API_URL}/v1/models",
                              {'Authorization': f'Bearer {XAI_API_KEY}'})
    router_health = router_future.result()
    primary_health = primary_future.result()
    xai_health = xai_future.result()
```

**Priority:** Low — happy path is already fast. This protects against thread starvation during backend failures.

**Files:** `src/app.py` (health endpoint only)

### 4. Eliminate Deep Copy of Messages

**Problem:** `copy.deepcopy(messages)` in `set_query()` copies the entire conversation history. For long multi-turn conversations with reasoning blocks, this is measurable CPU and memory overhead.

**Solution:** Snapshot messages as a JSON string immediately — we need JSON for the log file anyway. Store the string, write it directly in `save()`.

```python
# BEFORE (session_logger.py)
def set_query(self, messages):
    if messages:
        self.data['client_messages'] = copy.deepcopy(messages)

# AFTER
def set_query(self, messages):
    if messages:
        self._messages_json = json.dumps(messages, default=str)
```

Then in `save()`, embed `self._messages_json` directly instead of letting `json.dump()` re-serialize the deep copy.

**Files:** `src/session_logger.py`

### 5. Compute `date_context()` Once Per Request

**Problem:** `date_context()` is called 2–3 times per request (classification, enrichment injection, forward_request). Each call recomputes datetime, season, day type, etc.

**Solution:** Compute once in `chat_completions()` and pass through to all functions.

```python
date_ctx = date_context()
route = determine_route(data['messages'], session=session, date_ctx=date_ctx)
# ... pass date_ctx to forward_request ...
```

**Files:** `src/app.py`, `src/providers.py` (add `date_ctx` parameter to `determine_route`, `fetch_enrichment_context`, `forward_request`)

### 6. Fix Benchmark 3 (Long Context Test)

**Problem:** Benchmark 3 always fails because it sends `max_tokens: 100` directly to the primary vLLM endpoint. The Nano 30B reasoning model spends those 100 tokens on `<think>` blocks and returns `content: null` with `reasoning_content` only. The benchmark's `jq` extraction only checks `.choices[0].message.content`, so it sees an empty response and reports failure.

**Solution:** Update the Benchmark script to:
1. Check `reasoning_content` as a fallback (matching how the router and test script handle it).
2. Or remove `max_tokens` to match how the router actually sends requests to the primary model.

**Files:** `Benchmark`

---

## What NOT to Change

- **Flask/Werkzeug server** — Gunicorn would help under concurrent load, but this is single-user homelab. The current threading model is adequate.
- **Enrichment pipeline architecture** — The two-hop design (xAI context → primary inference) is inherently sequential. Total time ranges 13.8–30.9s, dominated by xAI web search (11–26s, 72–84%). High variance is on the xAI side. No shortcut without changing what enrichment does.
- **Classification model or prompt** — The 0.95–1.8s classification time is mostly `<think>` reasoning. Making the classifier faster requires a different model or prompt engineering to reduce reasoning depth — that's a separate investigation, not an implementation task.
- **Streaming passthrough** — Chunks pass through unchanged at near-zero overhead. The TTFT logging confirms provider-side TTFT is ~48ms (close to the 77–89ms direct-to-vLLM baseline). No optimization needed in the passthrough itself.

---

## Implementation Results

**Date:** 2026-02-16. All 6 items implemented, tests passing (26/26), benchmarks run.

### Verification

1. **`make test`** — 26/26 passed. All routes (MODERATE, COMPLEX, ENRICH, META) work correctly.
2. **`make benchmark`** — Benchmark 3 (long context) now passes. All other benchmarks unchanged.
3. **Session logs** — Speculative execution produces correct session JSONs with classification + provider_call steps.
4. **Speculative execution** — Primary routes log `speculative=true`. Non-primary routes log `Cancelled speculative primary (route=...)`.
5. **COMPLEX/ENRICH** — Speculative request properly cancelled; correct routing confirmed.
6. **Health endpoint** — Parallel checks working; ~150ms happy path unchanged.
7. **`cleanup_ms`** — Remains 0ms (deferred cleanup not yet triggered at low file count).

### Post-Benchmark Results

| Route | Baseline | After | Notes |
|-------|----------|-------|-------|
| MODERATE short (non-stream) | 1,181–1,426ms | ~1,790ms | See analysis below |
| MODERATE long (non-stream) | 1,803–3,873ms | ~2,167ms | Within baseline range |
| COMPLEX | 8,825–12,442ms | ~11,534ms | Within baseline range |
| ENRICH | 13,845–30,913ms | ~16,911ms | Within baseline range |
| Benchmark 3 (long context) | FAILED | **0.66s** | Fixed |

### Analysis: Why Short MODERATE Queries Didn't Improve in Benchmarks

The speculative execution works correctly — `total_ms ≈ max(classification_ms, inference_ms)` instead of `classification_ms + inference_ms`. However, classification (~1–3.8s) still dominates because:

1. **Non-streaming is bounded by classification time.** Speculation hides inference behind classification, but for short MODERATE queries where inference (~200–400ms) is shorter than classification (~1–3.8s), the total is still ≈ classification.
2. **Streaming TTFT is also bounded by classification.** We can't start piping speculative tokens until we confirm the route is primary — otherwise a COMPLEX/ENRICH misroute would send wrong tokens to the client. So streaming TTFT ≈ classification_ms, not ~48ms.
3. **The benchmark's `max_tokens` mismatch.** The benchmark sends `max_tokens: 10`, but the speculative request strips it (reasoning model needs full budget). This makes inference longer than the baseline's artificially capped response, inflating the measured latency.

The original Expected Impact estimates assumed classification could be fully hidden. In practice, classification is the longer task for short MODERATE queries, so it remains the bottleneck. The real win is for **longer MODERATE responses** where inference time exceeds classification time — there, speculation saves the full ~1–1.8s classification overhead.

### Future: Classification Latency

The remaining bottleneck is the Orchestrator 8B's `<think>` reasoning (~1–3.8s per classification). Potential approaches (not implemented — separate investigation):

- **Fast-path heuristic for trivially simple queries** — same pattern as meta detection, skipping classification entirely for unambiguous cases.
- **Non-reasoning cloud classifier** — e.g. `grok-4-1-fast-non-reasoning` for ~200–500ms classification. Tradeoff: adds per-request cloud API cost and external dependency.
- **Prompt engineering** — reduce the routing prompt complexity to encourage shorter `<think>` blocks.
- **Smaller/faster local classifier** — a non-reasoning model that doesn't produce `<think>` blocks.
