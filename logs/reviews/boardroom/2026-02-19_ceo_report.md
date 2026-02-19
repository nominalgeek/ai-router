# Session Review Report — Boardroom Mode
**Date**: 2026-02-19
**Sessions reviewed**: 18
**Period**: 2026-02-18T23:29:12 to 2026-02-18T23:41:04 (PST)

## Summary
- Total sessions: 18
- By route: primary=5, xai=0, enrich=2, meta=11
- Errors: 0 (no sessions had non-null `error` field)
- Issues found: 4

### Session Inventory

| ID | Time | Route | Classification | Query (truncated) |
|----|------|-------|----------------|-------------------|
| `1e385399` | 23:29:12 | enrich | ENRICH | "what will the weather be like tomorrow" |
| `028fb9cb` | 23:29:37 | meta | META | Follow-up suggestions (weather) |
| `907cd090` | 23:29:40 | meta | META | Title generation (weather) |
| `9f385aaa` | 23:29:43 | meta | META | Tag generation (weather) |
| `615a271c` | 23:29:57 | primary | SIMPLE | "shouldn't you ask me where i am first?" |
| `e1f9f461` | 23:29:59 | meta | META | Follow-up suggestions (weather+location) |
| `0c5256a4` | 23:31:48 | primary | [timeout] | "I am just outside of PDX in Happy Valley" |
| `05c35e62` | 23:31:58 | meta | META | Follow-up suggestions (weather+HV) |
| `bd4d8604` | 23:34:01 | enrich | ENRICH | "Is there any chance of snow later this week?" |
| `3be9fe87` | 23:34:22 | meta | META | Follow-up suggestions (snow) |
| `67142c34` | 23:39:47 | primary | SIMPLE | "what is light?" |
| `996cec17` | 23:39:51 | meta | META | Follow-up suggestions (light) |
| `cc1c175c` | 23:39:54 | meta | META | Title generation (light) |
| `ba6c6b39` | 23:39:58 | meta | META | Tag generation (light) |
| `692f3bc6` | 23:40:38 | primary | MODERATE | "What is sound?" |
| `50b479f3` | 23:40:45 | meta | META | Follow-up suggestions (light+sound) |
| `7a68d570` | 23:41:02 | primary | MODERATE | "can you mix up light and sound when high on drugs?" |
| `b46d7c65` | 23:41:04 | meta | META | Follow-up suggestions (synesthesia) |

## Issues

### 1. Classification Timeout — Location Follow-Up Misrouted
**Severity**: high
**Sessions affected**: `0c5256a4`
**Details**: The user said "I am just outside of PDX in Happy Valley" in the context of a weather conversation. The classifier timed out after 10,012ms (well above the 10s timeout threshold) and defaulted to `primary`. This query should have been classified as ENRICH — the user provided their location after being asked, and the conversation context makes clear they want a weather forecast for Happy Valley, OR. The primary model generated a response without real-time weather data.

The app.log confirms: `Routing classification timeout, defaulting to primary` and `SLOW_REQUEST session=0c5256a4 route=primary total_ms=10013 classification_ms=10012`.

**Root cause hypothesis**: The conversation context sent to the classifier was long (5 messages of weather data). The 8B classifier model's reasoning (`<think>` block) may have been excessively long, exceeding the 10s timeout. The query itself ("I am just outside of PDX in Happy Valley") is ambiguous without context — it's a location statement, not an explicit question. The classifier likely struggled to classify a statement (vs. a question) and spent too long reasoning.

**Recommendation**: This is a conversation-context-dependent misclassification caused by timeout. The query alone looks like a statement; the intent is only clear from conversation history. This is a hard edge case for the classifier.

### 2. Slow Classification — "What is sound?" Took 6,708ms
**Severity**: medium
**Sessions affected**: `692f3bc6`
**Details**: Classification of "What is sound?" took 6,708ms — well above the expected 1-3 second range. The app.log flagged this: `SLOW_REQUEST session=692f3bc6 route=primary total_ms=6709 classification_ms=6708`. The classifier's `<think>` block shows extensive deliberation between SIMPLE and MODERATE, going back and forth multiple times before landing on MODERATE.

This is the same pattern as session `67142c34` ("what is light?") which took 3,475ms — also above ideal but below the warning threshold. The classifier is spending too many reasoning tokens on ambiguous SIMPLE-vs-MODERATE boundaries.

**Recommendation**: The SIMPLE/MODERATE boundary is under-specified for general knowledge questions like "What is X?" The classifier's reasoning shows it getting stuck on whether a definition is "obvious" (SIMPLE) or requires "explanation of a concept" (MODERATE). Both "what is light?" and "what is sound?" are borderline cases that don't really matter — both route to `primary` anyway. However, the excessive reasoning time is a real cost.

### 3. Slow Classification — "Is there any chance of snow later this week?" Took 5,054ms
**Severity**: low
**Sessions affected**: `bd4d8604`
**Details**: Classification took 5,054ms for an ENRICH query. The classification was correct (ENRICH), so no routing error occurred. However, the classifier spent significant tokens reasoning about knowledge cutoff dates and whether the query needed real-time data — topics it shouldn't need to deliberate on given "this week" is an explicit ENRICH trigger word listed in the prompt.

The enrichment pipeline then added 14,987ms for context retrieval from xAI, bringing total to 20,048ms. The enrichment content was relevant and concise (609 chars about snow chances in Happy Valley, OR). The pipeline worked correctly here — the latency is within acceptable enrich-route bounds.

**Recommendation**: The classification latency is the only concern. The classifier is over-reasoning even on clear-cut ENRICH signals.

### 4. Inconsistent SIMPLE vs MODERATE for Equivalent Queries
**Severity**: low
**Sessions affected**: `67142c34`, `692f3bc6`
**Details**: "what is light?" was classified as SIMPLE (3,475ms) while "What is sound?" was classified as MODERATE (6,708ms). These are structurally identical queries — both ask for a definition of a fundamental physics concept. The inconsistency doesn't cause a functional problem (both route to `primary`), but it reveals the classifier's instability at this boundary.

The classifier's reasoning for "what is light?" landed on SIMPLE because it compared to the example "What is Python?" in the SIMPLE category. For "What is sound?", the classifier noticed the conversation context (previous answer about light was detailed) and reasoned that the user might expect a similar detailed answer, pushing it to MODERATE.

**Recommendation**: This is a cosmetic inconsistency since both routes go to the same backend. However, it indicates the SIMPLE/MODERATE boundary definition could be tightened.

## Route Quality Summary

### primary (5 sessions)
- Classifications: SIMPLE x2, MODERATE x2, [timeout] x1
- Average classification latency: 5,071ms (skewed by the 10s timeout)
- Excluding timeout: 3,700ms average — still above the ideal 1-3s range
- The speculative execution pattern is working well: all 5 primary sessions show `inference_ms=0` because the speculative primary response was already in flight during classification. This masks the slow classification from the user's perspective for primary-routed queries.
- One timeout-caused misroute (should have been ENRICH).

### enrich (2 sessions)
- Both correctly classified as ENRICH
- Classification latency: 2,638ms and 5,054ms
- Enrichment context retrieval: 20,345ms and 14,987ms
- Total latency: 22,990ms and 20,048ms
- Both enrichment contexts were relevant and well-scoped. The weather query (`1e385399`) returned multi-city forecasts; the snow query (`bd4d8604`) returned Portland-area specific snow data with source citations.

### meta (11 sessions)
- All correctly detected as meta-prompts via heuristic (0ms classification)
- All follow-up suggestion, title generation, and tag generation tasks completed successfully
- Average inference time: 1,546ms — fast and consistent
- All returned valid JSON in the expected format
- No false meta detections observed

### xai (0 sessions)
- No COMPLEX classifications in this review period. Not enough data to evaluate.

## Prompt Improvement Suggestions

### Classifier Verbosity / Over-Reasoning
The most consistent pattern across sessions is the classifier spending excessive tokens in `<think>` blocks. Examples:
- `67142c34`: 2,100+ characters of reasoning for "what is light?" — debating SIMPLE vs MODERATE
- `692f3bc6`: 2,200+ characters for "What is sound?" — same debate, different conclusion
- `bd4d8604`: 1,600+ characters for "Is there any chance of snow this week?" — deliberating despite "this week" being an explicit ENRICH trigger

The routing system prompt says "You must respond with ONLY ONE WORD" but the model still produces extensive `<think>` blocks (which are expected for a reasoning model — the instruction only constrains the output after `</think>`). The reasoning itself is the latency bottleneck.

One possible mitigation: add guidance in the system prompt to be fast and not over-deliberate. However, this is a model-behavior issue more than a prompt issue — the Nemotron Orchestrator 8B is a reasoning model that will think before answering regardless.

### SIMPLE/MODERATE Boundary
The current SIMPLE definition ("basic questions with obvious answers") and MODERATE definition ("explanations of concepts") create a gray zone for definitional questions like "What is X?". Since both categories route to the same backend (`primary`), this is functionally irrelevant but wastes classifier time. Adding "What is [concept]?" as an explicit SIMPLE example could reduce deliberation time for this common query pattern.

## Proposals

### Proposal 1: Add definitional "What is X?" to SIMPLE examples
**Problem**: The classifier spends 3-7 seconds deliberating whether "What is [concept]?" queries are SIMPLE or MODERATE, producing inconsistent results (SIMPLE for "light", MODERATE for "sound"). Both route to `primary` regardless, but the deliberation burns unnecessary latency.
**Evidence**: Sessions `67142c34` (3,475ms, classified SIMPLE), `692f3bc6` (6,708ms, classified MODERATE), `7a68d570` (2,571ms — included for baseline comparison of faster classification on a clearer MODERATE query)
**Target file**: `config/prompts/routing/request.md`
**Proposed edit**:
```diff
 SIMPLE: Greetings, casual chat, basic questions with obvious answers (NOT questions about today, current time, current date, or anything happening right now)
-Examples: "Hello", "What is Python?", "How are you?"
+Examples: "Hello", "What is Python?", "How are you?", "What is light?", "What is gravity?"
```
**Rationale**: Adding concrete science-definition examples to SIMPLE makes the boundary clearer for the classifier. "What is Python?" is already there but the classifier treats it differently from "What is light?" because it sees Python as a named entity vs. a concept. Adding physical-concept examples closes this gap. Both SIMPLE and MODERATE route to `primary`, so even if a MODERATE-worthy query gets classified as SIMPLE, there is zero routing impact.
**Risk assessment**: Minimal. A truly complex question like "What is the nature of consciousness?" might get pulled toward SIMPLE if the classifier over-generalizes from these examples. However, such questions are qualitatively different enough (multi-domain, philosophical) that the MODERATE/COMPLEX definitions should still apply. The "when in doubt, choose the higher one" instruction provides a safety net.

### Proposal 2: Add implicit-location-reference guidance to ENRICH examples
**Problem**: When a user provides their location in a conversational follow-up (e.g., "I am just outside of PDX in Happy Valley") after being asked for it, the classifier struggles to recognize this as an implicit weather/ENRICH query. The classifier timed out (10,012ms) and the query defaulted to `primary` without enrichment, meaning the user got a response without real-time weather data for their location.
**Evidence**: Sessions `0c5256a4` (timeout, should have been ENRICH), `1e385399` (correctly classified ENRICH for initial weather query), `bd4d8604` (correctly classified ENRICH for follow-up snow query)
**Target file**: `config/prompts/routing/request.md`
**Proposed edit**:
```diff
 ENRICH: Questions that require current events, recent news, real-time data, today's date/time, information after a knowledge cutoff date, OR factual details about specific real-world entities (named places, schools, businesses, organizations, people). ANY question using words like "today", "tonight", "tomorrow", "yesterday", "right now", "current", "latest", "recent", "this week", "this year", "this month", "next week", or referencing a specific future/present date. Also applies to questions about whether a specific place is open/closed, business hours, school schedules, event times, or anything that depends on up-to-date real-world status. Also applies to questions asking for facts, history, details, or information about a specific named real-world entity (a school, restaurant, company, person, location, etc.) — these need verified data, not guesses.
-Examples: "What happened in the news today?", "What is the current price of Bitcoin?", "Who won the latest election?", "What are the newest features in Python 3.14?", "What is today's date?", "What day is it?", "Is the library open tomorrow?", "What's the schedule for next week?", "Tell me about the history of [specific school]", "What is [company name] known for?"
+Examples: "What happened in the news today?", "What is the current price of Bitcoin?", "Who won the latest election?", "What are the newest features in Python 3.14?", "What is today's date?", "What day is it?", "Is the library open tomorrow?", "What's the schedule for next week?", "Tell me about the history of [specific school]", "What is [company name] known for?", "I'm in [city/location]" (when conversation context involves weather, directions, or local info)
```
**Rationale**: The classifier currently only looks for explicit question patterns or temporal keywords. When a user provides a location in response to being asked, the query is a statement, not a question, and contains no temporal keywords. Adding a contextual example teaches the classifier that location statements within real-time-data conversations should trigger ENRICH. The parenthetical "(when conversation context involves weather, directions, or local info)" scopes it to avoid false positives on location mentions in unrelated conversations.
**Risk assessment**: Moderate. The classifier receives conversation context, so it *should* be able to make this inference. The risk is that any mention of a location (e.g., "I grew up in Portland") in a non-weather/non-local-info conversation gets over-classified as ENRICH. The parenthetical qualifier should mitigate this, but the classifier's ability to apply nuanced conditional rules is uncertain given the timeout issue suggests it already struggles with complex reasoning. This proposal may not prevent the timeout — the root cause may be the classifier over-reasoning on ambiguous inputs regardless of examples. Human review recommended on whether this is worth the added prompt complexity.
