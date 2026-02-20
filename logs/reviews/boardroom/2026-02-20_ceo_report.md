# Session Review Report — Boardroom Mode
**Date**: 2026-02-20
**Sessions reviewed**: 30
**Period**: 2026-02-18T23:29:12 to 2026-02-19T18:15:46 (PST)

## Summary
- Total sessions: 30
- By route: primary=7, xai=0, enrich=4, meta=19
- Errors: 0 (no sessions had non-null `error` field)
- Issues found: 3

### Session Inventory

| ID | Time | Route | Classification | Query (truncated) | Classification ms | Total ms |
|----|------|-------|----------------|-------------------|-------------------|----------|
| `1e385399` | 02-18 23:29:12 | enrich | ENRICH | "what will the weather be like tomorrow" | 2638 | 22990 |
| `028fb9cb` | 02-18 23:29:37 | meta | META | Follow-up suggestions (weather) | 0 | 2949 |
| `907cd090` | 02-18 23:29:40 | meta | META | Title generation (weather) | 0 | 2606 |
| `9f385aaa` | 02-18 23:29:43 | meta | META | Tag generation (weather) | 0 | 9093 |
| `615a271c` | 02-18 23:29:57 | primary | SIMPLE | "shouldn't you ask me where i am first?" | 2148 | 2149 |
| `e1f9f461` | 02-18 23:29:59 | meta | META | Follow-up suggestions (weather+location) | 0 | 3448 |
| `0c5256a4` | 02-18 23:31:48 | primary | [timeout] | "I am just outside of PDX in Happy Valley" | 10012 | 10013 |
| `05c35e62` | 02-18 23:31:58 | meta | META | Follow-up suggestions (weather+HV) | 0 | 3289 |
| `bd4d8604` | 02-18 23:34:01 | enrich | ENRICH | "Is there any chance of snow later this week?" | 5054 | 20048 |
| `3be9fe87` | 02-18 23:34:22 | meta | META | Follow-up suggestions (snow) | 0 | 3386 |
| `67142c34` | 02-18 23:39:47 | primary | SIMPLE | "what is light?" | 3475 | 3476 |
| `996cec17` | 02-18 23:39:51 | meta | META | Follow-up suggestions (light) | 0 | 2624 |
| `cc1c175c` | 02-18 23:39:54 | meta | META | Title generation (light) | 0 | 3978 |
| `ba6c6b39` | 02-18 23:39:58 | meta | META | Tag generation (light) | 0 | 2676 |
| `692f3bc6` | 02-18 23:40:38 | primary | MODERATE | "What is sound?" | 6708 | 6709 |
| `50b479f3` | 02-18 23:40:45 | meta | META | Follow-up suggestions (light+sound) | 0 | 2213 |
| `7a68d570` | 02-18 23:41:02 | primary | MODERATE | "can you mix up light and sound when high on drugs?" | 2571 | 2572 |
| `b46d7c65` | 02-18 23:41:04 | meta | META | Follow-up suggestions (synesthesia) | 0 | 3442 |
| `2bec6c42` | 02-19 18:13:55 | enrich | ENRICH | "how goes the weather? who are you" | 2544 | 17469 |
| `453e2c69` | 02-19 18:14:15 | meta | META | Follow-up suggestions (weather+identity) | 0 | 2270 |
| `28944558` | 02-19 18:14:17 | meta | META | Title generation (weather) | 0 | 4237 |
| `c6c33010` | 02-19 18:14:21 | meta | META | Tag generation (weather) | 0 | 6808 |
| `dfcc2b9e` | 02-19 18:14:59 | primary | SIMPLE | "but whats your name dude!?" | 1661 | 1662 |
| `68035ea6` | 02-19 18:15:01 | primary | SIMPLE | "but whats your name dude?!" (duplicate) | 2104 | 2105 |
| `853f9e48` | 02-19 18:15:01 | meta | META | Follow-up suggestions | 0 | 4740 |
| `8e5b3565` | 02-19 18:15:04 | meta | META | Follow-up suggestions | 0 | 3622 |
| `f6eeb570` | 02-19 18:15:11 | primary | SIMPLE | "i will call you mini-me" | 1964 | 1965 |
| `46eae1b1` | 02-19 18:15:13 | meta | META | Follow-up suggestions (capabilities) | 0 | 2726 |
| `bc988c3a` | 02-19 18:15:33 | enrich | ENRICH | "tell me something you shouldnt" | 7298 | 12808 |
| `27786eea` | 02-19 18:15:46 | meta | META | Follow-up suggestions (boundaries) | 0 | 2326 |

## Issues

### 1. False ENRICH: "tell me something you shouldnt" Misclassified as ENRICH
**Severity**: high
**Sessions affected**: `bc988c3a`
**Details**: The user said "tell me something you shouldnt" — a conversational/playful boundary-testing query with no real-time data requirement whatsoever. The classifier classified this as ENRICH after 7,298ms of deliberation. The classifier's `<think>` block (visible in `response_content`) shows it struggling to categorize the query, going through all four categories, and eventually landing on ENRICH by process of elimination rather than because the query matches ENRICH criteria.

This triggered the full enrichment pipeline: xAI was called (5,502ms) to retrieve "real-time context" for a query that doesn't need any. The xAI response was appropriately dismissive ("Nice try, Mini-Me sticks to the facts. What's your real question?"), but this 65-character non-answer was then injected into the primary model's system prompt as "verified, real-time information retrieved just now" — telling the primary model to treat a joke deflection as ground truth.

The downstream effect: the primary model received a system prompt containing `IMPORTANT: The following is verified, real-time information... Nice try, Mini-Me sticks to the facts. What's your real question?` — which is confusing nonsense context for a boundary-testing query. The total cost was 12,808ms and a wasted xAI API call.

This should have been classified as SIMPLE (casual chat, boundary testing).

**Recommendation**: The classifier's "when in doubt, choose ENRICH" guidance in `routing/system.md` is too aggressive for conversational queries that don't match any category well. The classifier's reasoning shows it defaulting to ENRICH when it can't find a clear fit, rather than falling back to SIMPLE for casual/conversational queries.

### 2. Classification Timeout — Location Follow-Up Misrouted (Carry-Forward)
**Severity**: high
**Sessions affected**: `0c5256a4`
**Details**: Previously identified in BR-0001. The user said "I am just outside of PDX in Happy Valley" in a weather conversation. The classifier timed out after 10,012ms and defaulted to `primary` without enrichment. This query should have been ENRICH — the user was providing their location so the weather conversation could continue with real data.

The app.log confirms: `Routing classification timeout, defaulting to primary`.

**Status**: No new instances of this timeout pattern in the Feb 19 sessions. Still only 1 occurrence, below the 3-session minimum for a prompt-change proposal. Documenting for pattern tracking. The previous cycle's Challenger and QA correctly noted that prompt changes are unlikely to fix a timeout-caused-by-over-reasoning issue.

### 3. Slow Classification Persists Across Both Days
**Severity**: medium
**Sessions affected**: `692f3bc6` (6,708ms), `bc988c3a` (7,298ms), `bd4d8604` (5,054ms), `0c5256a4` (10,012ms timeout), `67142c34` (3,475ms)
**Details**: Five of the 11 classified sessions (excluding meta, which bypasses classification) had classification times above 3 seconds. Two were above 5 seconds. One timed out at 10 seconds.

The pattern from the previous cycle holds: the classifier produces long `<think>` blocks when the query doesn't cleanly match a single category. Fast classifications (1.6-2.5s) occur on clear-cut queries like greetings ("but whats your name dude!?" at 1,661ms) and weather questions ("how goes the weather?" at 2,544ms). Slow classifications occur on ambiguous or edge-case queries.

This is a model-behavior characteristic, not a prompt issue. The previous cycle's Challenger and QA correctly diagnosed this as a property of the 8B reasoning model.

**Recommendation**: Accept this as inherent to the reasoning model. The speculative execution pattern already masks latency for primary-routed queries (classification and primary inference run in parallel, so the user doesn't wait for classification). The latency is only user-visible for enrich routes where classification must complete before the enrichment call begins. No prompt change would help — the model reasons as long as it reasons.

## Route Quality Summary

### primary (7 sessions)
- Classifications: SIMPLE x5, MODERATE x2 (plus 1 timeout defaulting to primary)
- Classification latency range: 1,661ms – 10,012ms (timeout)
- Median classification latency (excluding timeout): 2,148ms
- All primary-routed sessions used speculative execution (`inference_ms=0`), meaning classification latency was hidden from the user. The speculative pattern is performing well.
- SIMPLE classifications were appropriate: greetings, name questions, nickname setting
- MODERATE classifications were appropriate: "What is sound?" and synesthesia question both benefit from explanation-level depth
- The timeout session (`0c5256a4`) was a misroute — should have been ENRICH

### enrich (4 sessions)
- 3 correctly classified: weather queries (`1e385399`, `bd4d8604`, `2bec6c42`)
- 1 misclassified: "tell me something you shouldnt" (`bc988c3a`) — should have been SIMPLE
- Classification latency: 2,544ms – 7,298ms
- Enrichment context retrieval from xAI: 5,502ms – 20,345ms
- Total latency: 12,808ms – 22,990ms
- For the 3 correct ENRICH sessions, enrichment context was relevant and well-scoped (weather data matching location and timeframe)
- For the 1 misclassified session, the xAI enrichment returned a 65-character deflection that was useless as context

### meta (19 sessions)
- All 19 correctly detected via heuristic (0ms classification)
- All completed successfully with `finish_reason=stop`
- Inference latency: 881ms – 4,041ms (median ~1,500ms)
- All returned valid JSON (follow-up suggestions, titles, tags)
- No false meta detections observed
- One slow meta session: `c6c33010` at 6,808ms total (4,041ms inference for tag generation) — flagged as SLOW_REQUEST in app.log but still within acceptable bounds

### xai (0 sessions)
- No COMPLEX classifications in this review period. Insufficient data to evaluate.

## Prompt Improvement Suggestions

### Context from Previous Cycle (BR-0001)
The previous cycle proposed two prompt edits (definitional "What is X?" SIMPLE examples, implicit-location ENRICH guidance). Both were CHALLENGED — the Challenger correctly identified that:
1. Adding examples doesn't fix classifier over-reasoning (root cause is model behavior)
2. The timeout-caused misroute has only 1 instance (below evidence threshold)

The QA Validator concurred and recommended focusing on solution design rather than evidence collection.

### New Observation: ENRICH as a "Catch-All" for Unrecognizable Queries
Session `bc988c3a` reveals a new pattern: the classifier defaults to ENRICH when it cannot classify a query into SIMPLE, MODERATE, or COMPLEX. The routing system prompt says "When in doubt about whether a query needs current information or specific factual details about a real-world entity, choose ENRICH." The classifier interpreted "tell me something you shouldnt" as "none of the above" and applied the ENRICH doubt-default.

This is the inverse of the typical ENRICH concern (missing real-time queries). Here, a non-real-time query was pulled *into* ENRICH because the classifier's doubt-resolution heuristic only points upward. The system prompt has two doubt-escalation rules:
1. "When in doubt between complexity levels, choose the higher one" (SIMPLE → MODERATE → COMPLEX)
2. "When in doubt about whether a query needs current information... choose ENRICH"

There is no guidance for "when the query doesn't fit any category, default to SIMPLE" — which is what casual/conversational/boundary-testing queries need. This gap is what caused the misclassification.

## Proposals

### Proposal 1: Add SIMPLE-default guidance for conversational queries that don't match other categories
**Problem**: The classifier's doubt-escalation rules ("when in doubt, choose the higher one" and "when in doubt about ENRICH, choose ENRICH") cause it to default to ENRICH for conversational queries that don't cleanly fit any category. This wastes xAI API calls and injects nonsensical enrichment context into the primary model's system prompt.
**Evidence**: Sessions `bc988c3a` ("tell me something you shouldnt" — classified ENRICH, should be SIMPLE), `615a271c` ("shouldn't you ask me where i am first?" — correctly classified SIMPLE, showing the classifier *can* handle conversational queries when they're more recognizable), `dfcc2b9e` ("but whats your name dude!?" — correctly classified SIMPLE, same pattern)
**Target file**: `config/prompts/routing/system.md`
**Proposed edit**:
```diff
 Be decisive. When in doubt between complexity levels, choose the higher one. When in doubt about whether a query needs current information or specific factual details about a real-world entity, choose ENRICH.
+
+Exception: if the query is clearly conversational, playful, or testing boundaries (not asking for information, explanations, or real-world data), classify it as SIMPLE regardless of other doubt. ENRICH is only for queries that genuinely need external data — not for queries that simply don't fit other categories.
```
**Rationale**: The current prompt has an escalation-only doubt-resolution path: doubt about complexity goes up, doubt about real-time data goes to ENRICH. There is no "down" path for queries that are clearly casual conversation. The proposed addition creates an explicit exception: conversational/playful queries default to SIMPLE, not ENRICH. This does not change the existing escalation rules for genuine queries — it only adds a floor for clearly non-informational requests.

The classifier's `<think>` block in `bc988c3a` shows it explicitly going through all four categories, finding no match, and defaulting to ENRICH via the "when in doubt" rule. With this change, the classifier would have a third option: "this is just conversation → SIMPLE."
**Risk assessment**: Low. The added text is scoped to "clearly conversational, playful, or testing boundaries" — a narrow and recognizable category. The risk is that a genuinely ENRICH-worthy query phrased conversationally ("hey tell me what's happening in the world") gets downgraded to SIMPLE. However, that query contains the temporal signal "what's happening" which should still trigger ENRICH under the existing rules. The new text only applies when the query has *no* informational content — pure conversational play. The two correctly-classified SIMPLE sessions (`615a271c`, `dfcc2b9e`) demonstrate that the classifier already applies this logic for recognizable conversational queries; this proposal just makes it explicit for edge cases.
