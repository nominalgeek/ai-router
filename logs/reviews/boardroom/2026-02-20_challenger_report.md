# Challenger Report
**Date**: 2026-02-19
**CEO report reviewed**: `logs/reviews/boardroom/2026-02-20_ceo_report.md`
**Proposals evaluated**: 1

## Summary
- Proposals ACCEPTED: 0
- Proposals CHALLENGED: 1
- Proposals NEEDS_EVIDENCE: 0

## Proposal Evaluations

### Proposal: Add SIMPLE-default guidance for conversational queries that don't match other categories
**Target file**: `config/prompts/routing/system.md`
**Verdict**: CHALLENGED

**Evidence check**:
- Sessions cited: `bc988c3a`, `615a271c`, `dfcc2b9e`
- Sessions verified: All three read and confirmed
- Evidence assessment: **Partially accurate with critical flaws**

The CEO's characterization of session `bc988c3a` is accurate: "tell me something you shouldnt" was classified as ENRICH (7,298ms classification time), triggered a full enrichment pipeline with xAI call (5,502ms), and received a 65-character deflection ("Nice try, Mini-Me sticks to the facts. What's your real question?") that was then injected into the primary model's system prompt as "verified, real-time information." This is indeed a misclassification.

However, the supporting evidence sessions (`615a271c` and `dfcc2b9e`) do NOT support the proposal — they demonstrate the **opposite**. Both were correctly classified as SIMPLE. The CEO presents them as evidence that "the classifier *can* handle conversational queries when they're more recognizable," but this undermines the premise that the classifier needs new guidance. If it already correctly classifies conversational queries 2 out of 3 times without the proposed text, then the proposed change is addressing a 1-session edge case, not a systemic pattern.

**Critical flaw**: The proposal cites 3 sessions, but 2 of them are correct classifications that contradict the need for the change. The actual evidence is **1 session** (`bc988c3a`), which falls below the 3-session minimum threshold established in `review-board.yaml`.

**Regression analysis**:

The proposed text introduces a dangerous exception: "if the query is clearly conversational, playful, or testing boundaries (not asking for information, explanations, or real-world data), classify it as SIMPLE regardless of other doubt."

This creates **high regression risk** for queries that are phrased conversationally but genuinely need enrichment. Examples of queries that could be misrouted by this rule:

1. **"hey tell me what's happening in the world right now"** — conversational phrasing ("hey"), but needs real-time news data (ENRICH)
2. **"dude what's the weather like today"** — conversational phrasing ("dude"), but needs real-time weather data (ENRICH)
3. **"can you tell me something cool about [company name]"** — playful phrasing ("something cool"), but requires factual data about a specific entity (ENRICH)
4. **"what's going on with bitcoin lol"** — conversational/playful phrasing ("lol"), but needs current price data (ENRICH)

The proposed exception tells the classifier to prioritize conversational tone over informational content. This inverts the classification logic: instead of "does this query need real-time data?", it becomes "does this query *sound* casual?" A query can be both casual in tone AND require enrichment.

The CEO's own evidence proves this risk: session `615a271c` ("shouldn't you ask me where i am first?") is conversational in tone but was correctly classified as SIMPLE because it's genuinely not asking for information. The classifier's reasoning block explicitly states: "The user is pointing out a procedural step that wasn't taken. There's no request for current weather, news, or specific factual data... It's a simple conversational point." The classifier already understands the distinction between conversational tone and informational content — it doesn't need explicit guidance to "default conversational queries to SIMPLE" because it's already doing it correctly when the query is genuinely conversational.

The problem with `bc988c3a` is different: the classifier's think block shows it went through all four categories, found no match, and applied the existing "when in doubt about ENRICH, choose ENRICH" rule. The root cause isn't that the classifier needs a conversational-default rule — it's that the classifier couldn't determine whether "tell me something you shouldnt" is asking for information at all. The query is ambiguous: is it testing boundaries (SIMPLE), asking for secret knowledge (MODERATE explanation of policies), or asking about controversial real-world facts (ENRICH)? The classifier's 7-second deliberation shows it genuinely couldn't decide.

**The proposed fix addresses a symptom (ENRICH-by-doubt-default) by creating a new primary criterion (conversational tone), which will cause the classifier to misroute queries where tone and content diverge.**

**Alternative framing**: The real issue is that the doubt-escalation rules have no "none of the above" path. The existing text says:
- "When in doubt between complexity levels, choose the higher one"
- "When in doubt about whether a query needs current information... choose ENRICH"

But there's no guidance for "when the query is genuinely ambiguous or unanswerable, default to SIMPLE." The CEO's proposed exception tries to create this path by using conversational tone as the trigger, but tone is the wrong criterion — it's orthogonal to whether a query needs enrichment.

**Architectural check**: Pass. The proposal stays within `config/prompts/routing/system.md` and maintains classifier-only scope.

**Verdict rationale**:

I am CHALLENGING this proposal for three reasons:

1. **Insufficient evidence**: Only 1 session demonstrates the claimed problem. The other 2 cited sessions are correct classifications that prove the classifier already handles conversational queries appropriately. This falls below the 3-independent-session minimum for prompt changes.

2. **High regression risk**: The proposed exception prioritizes conversational tone over informational content, which will misroute queries that are phrased casually but genuinely need enrichment. The CEO's own evidence demonstrates that the classifier already distinguishes between tone and content when the query is unambiguous — the issue is specifically with queries that are genuinely ambiguous in intent, and tone is not a reliable signal for resolving that ambiguity.

3. **Disproportionate change**: A two-sentence addition to the routing prompt that introduces a new primary classification criterion (tone) is a large change for a 1-session edge case. The CEO correctly diagnosed the symptom (no "down" path from doubt), but the proposed cure (conversational-tone exception) treats tone as a proxy for "doesn't need information," which breaks when tone and content diverge.

**Path forward for CEO**: If the CEO wishes to revise and re-propose, they should either (a) wait for more sessions demonstrating the same pattern (ambiguous-query → ENRICH-by-doubt-default), or (b) propose a narrower edit that addresses the doubt-default path without introducing tone-based classification. For example: "When the query is genuinely unclear or unanswerable (not requesting specific information, explanations, or actions), classify as SIMPLE." This keeps the focus on informational content, not tone.

## Cycle Recommendation

No proposals survived challenge — cycle ends with no changes.

The single proposal identified a real edge case (1 session) but proposed a fix with high regression risk and insufficient supporting evidence. The CEO should collect more sessions showing the same pattern or revise the approach to avoid tone-based classification criteria.
