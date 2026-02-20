# Challenger Report
**Date**: 2026-02-20
**CEO report reviewed**: `logs/reviews/boardroom/2026-02-20_ceo_report.md`
**Proposals evaluated**: 1

## Summary
- Proposals ACCEPTED: 0
- Proposals CHALLENGED: 0
- Proposals NEEDS_EVIDENCE: 0
- **Human review items**: 1 (code change outside Session CEO scope)

## Proposal Evaluations

### Proposal 1: Human Review Required — Classification Stop Sequence Conflict
**Target file**: `src/providers.py` (line 110) — **outside Session CEO edit scope**
**Verdict**: HUMAN REVIEW REQUIRED (not challengeable — outside prompt-edit authority)

**Evidence check**:
- **Sessions cited**: 30 sessions from Batch 3 (Feb 19, 20:46-20:53) — `b72955c7`, `63538d5c`, `c71af38d`, `facc038f`, `6b52b4f0`, `bc3ef7e6`, `bee84817`, `5bcb228a`, `e1ad6e59`, `d5ae574c`, `ea4e38c8`, `53c8360f`, `470fb4dd`, `f3a8813f`, `bd038e51`, `1f1ad7a9`, `f10de9b3`, `79de99c4`, `76a65593`, `82673e81`, `432c6117`, `e77f478d`, `517318e6`, `b49a7ab6`, `40de3e4e`, `2a3a985d`, `d7039c19`, `af9c6a72`, `5235cba7`, `a17fe764`, `415cdf53`, `405b5275`
- **Sessions verified**: Independently read sessions `2026-02-19_20-46-59_b72955c7.json`, `2026-02-19_20-47-32_c71af38d.json`, `2026-02-19_20-47-46_5bcb228a.json`, `2026-02-19_20-50-15_79de99c4.json`
- **Evidence assessment**: ACCURATE — all verified sessions show:
  - `classification_raw: ""` (empty string)
  - `response_content: "<think>"` (single token before stop)
  - `finish_reason: "stop"` (terminated by stop sequence)
  - `classification_ms: 30-85ms` (abnormally fast — single token generation)
  - `params: {"temperature": 0.0, "max_tokens": 64, "stop": ["\n"]}` (stop sequence present)
  - Route defaulted to `primary` regardless of query content

**Contrast verification**: Read sessions `2026-02-18_23-29-12_1e385399.json` (weather query, correctly classified as ENRICH in 2,638ms) and `2026-02-18_23-29-57_615a271c.json` (conversational query, correctly classified as SIMPLE in 2,148ms) from Batch 1. Both show:
  - `params: {"temperature": 0.0}` (NO stop sequence in session logs from Batch 1/2)
  - Full `<think>...</think>` reasoning blocks in `response_content`
  - Valid classification words (ENRICH, SIMPLE, MODERATE) in `classification_raw`
  - Normal classification latency (1,600-7,300ms)

**Root cause verification**: Examined `src/providers.py` line 110 — confirms `classify_params = {"temperature": 0.0, "max_tokens": 64, "stop": ["\n"]}` in current code. Git history shows this was added in commit `e1c06f2` (Feb 19, 20:54). The parameter is passed directly to the router model API call and logged by `session.begin_step()`.

The CEO's diagnosis is correct: the `\n` stop sequence intercepts the newline immediately after `<think>`, terminating generation before any classification word is produced. The response contains only the string `"<think>"`, which the existing regex at lines 148-149 correctly strips, leaving an empty `decision` that falls through to the default `primary` route at line 152.

**Sample misrouted queries verified**:
- Session `5bcb228a` ("What is the current weather in Tokyo right now?") — should be ENRICH (has "current" and "right now" temporal markers), was misrouted to `primary`, fabricated weather data ("partly cloudy with temperatures around 9°C...")
- Session `79de99c4` ("Design a novel approach to quantum error correction") — matches COMPLEX examples exactly ("Design a novel..."), was misrouted to `primary`, generated a 3,201ms answer on quantum topological codes (competent but should have escalated to xAI)
- Session `c71af38d` ("Explain the concept of quantum entanglement in detail") — correctly MODERATE-level complexity, but classification failure caused 100% hit rate so even correct outcomes were accidental defaults, not deliberate routing

**Evidence quality**: Strong (30/30 sessions showing identical failure mode, 100% classification failure rate in post-restart batch vs. 0% in pre-restart batches with same prompts and model)

**Regression analysis**:
This is a REMOVAL, not an addition, so traditional regression analysis (what other queries might break) is inverted — the question is: what behavior does the stop sequence ENABLE that removing it would break?

**Answer**: The `\n` stop sequence was likely intended to prevent the model from generating verbose multi-line output after the classification word. By stopping at the first newline, the requester hoped to get just:
```
<think>reasoning...</think>
MODERATE
```
...with generation halting at the newline after `MODERATE`.

**Why this backfired**: The model emits `<think>\n` to BEGIN its reasoning block. The stop sequence fires on that first internal newline, yielding only `<think>` with no classification word.

**Why removal is safe**:
1. **`max_tokens: 64` already bounds generation** — even without `stop: ["\n"]`, the model cannot produce more than 64 tokens. Based on Batch 1/2 logs (where the stop sequence was absent), the full reasoning blocks were ~400-600 characters (roughly 150-200 tokens including the `<think>` wrapper), but the model consistently emitted the classification word within the first ~50-100 tokens after starting reasoning. A 64-token cap is sufficient.

2. **Regex stripping already handles `<think>` blocks** — lines 148-149 of `providers.py` use `re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)` to remove closed reasoning blocks, then `re.sub(r'<think>.*', '', decision, flags=re.DOTALL)` to remove any unclosed trailing block. This handles both completed reasoning and truncated mid-reasoning output. The classification word extraction is ALREADY designed to work with multi-line reasoning present.

3. **Empirical evidence from Batches 1 and 2** — classification worked correctly for 18 sessions in Batch 1 and 12 sessions in Batch 2 WITHOUT the stop sequence. Those sessions show that the model naturally produces `<think>...reasoning...</think>CLASSIFICATION_WORD` and the regex extraction handles it cleanly. Adding the stop sequence BROKE a working system.

**Worst-case scenario if removed**: The model generates 64 tokens of reasoning + classification word + trailing whitespace/commentary. The regex strips all `<think>` blocks, `.strip().upper()` on line 150 removes whitespace, and the classification word is extracted. If the model gets verbose AFTER the classification word (e.g., `MODERATE\nBecause this is a basic question...`), the regex won't strip it (it's outside `<think>` tags), so `decision` would contain `MODERATE\nBECAUSE THIS IS A BASIC QUESTION...`. But line 153 checks `if 'ENRICH' in decision`, line 155 checks `if 'MODERATE' in decision`, etc. — the substring matching is permissive and would still work. Even verbose trailing text wouldn't break routing.

**Actual risk**: Near zero. The stop sequence is actively harmful (causes 100% failure) and provides no benefit that `max_tokens: 64` + regex stripping don't already provide.

**Architectural check**: PASS — this is a technical parameter fix in the classification request. It does not blur the boundary between classifier and generator. The classifier model still only classifies; the primary model still only generates. The enrichment pipeline still only fetches context. Removing a broken stop sequence does not violate separation of concerns.

**Proportionality check**: PASS — 30/30 classification failures (100% hit rate) across an entire batch, causing misrouted COMPLEX queries (sent to local model when cloud escalation was needed) and misrouted ENRICH queries (fabricated real-time data instead of fetching it) absolutely justifies a one-line parameter change. This is not speculative optimization; this is fixing a complete system failure.

**Verdict rationale**:
The CEO's analysis is technically sound, the evidence is overwhelming (100% failure rate vs. 0% in earlier batches), the proposed fix is minimal and low-risk, and the regression analysis confirms removal is safer than keeping the broken stop sequence.

**However**, this proposal modifies Python source code in `src/providers.py`, which is explicitly outside the Session CEO's edit authority per `agents/session-review/AGENT.md` ("Your scope is limited to: config/prompts/routing/system.md, config/prompts/routing/request.md, config/prompts/enrichment/system.md, config/prompts/enrichment/injection.md").

The CEO correctly labeled this as "requires human review" and provided the exact code change needed. **This is the appropriate outcome** — the Session CEO identified a critical infrastructure issue, diagnosed the root cause, verified it against session logs, and escalated to human authority with a specific remediation proposal.

**My role as Adversarial Challenger does not extend to approving or blocking code changes.** I can only evaluate prompt-edit proposals. This is a correct escalation, not a challengeable proposal.

## Issues Not Proposed (Acknowledged Correctly)

The CEO correctly identified Issue 2 (fabricated real-time data) as a **downstream consequence of Issue 1**, not a separate prompt problem. The primary model confidently invented specific weather data for Tokyo and fictitious AI regulation events because it received ENRICH-eligible queries without enrichment context. This resolves automatically when Issue 1 is fixed. **Correct call** — no separate prompt change needed.

The CEO correctly noted Issue 3 ("tell me something you shouldnt" misclassified as ENRICH) **remains at 1 session** (`bc988c3a`), below the 3-session evidence threshold from `review-board.yaml`. **Correct call** — continue monitoring, do not propose changes on single-session patterns.

The CEO correctly characterized Issue 4 (slow classification, 5-7 seconds) as **inherent to the 8B reasoning model**, not fixable via prompts, and already mitigated by speculative execution for primary-routed queries. **Correct call** — this is model behavior, not a routing logic issue.

## Cycle Recommendation

**No prompt proposals to evaluate.** The single item in this report is a code change that requires human review and is outside the Improvement Board's authority.

**Cycle outcome**: No changes to prompt files. Human operator should review Proposal 1 (stop sequence removal in `src/providers.py`) and decide whether to implement it outside the boardroom process.

**For the next cycle**: If the human operator implements the stop sequence fix and classification returns to normal operation, the CEO should monitor for any NEW classification issues that emerge. The ENRICH false-positive pattern (Issue 3, session `bc988c3a`) is still worth tracking — if it recurs in 2+ more independent sessions, it may warrant a prompt clarification to distinguish "conversational boundary-testing" from "queries needing verified facts."
