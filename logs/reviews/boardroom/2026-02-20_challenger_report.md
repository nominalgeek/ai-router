# Challenger Report
**Date**: 2026-02-19
**CEO report reviewed**: `logs/reviews/boardroom/2026-02-20_ceo_report.md`
**Proposals evaluated**: 1

## Summary
- Proposals ACCEPTED: 0
- Proposals CHALLENGED: 1
- Proposals NEEDS_EVIDENCE: 0

## Proposal Evaluations

### Proposal: Remove max_tokens Constraint from Classification Requests
**Target file**: `src/providers.py` (line 110)
**Verdict**: CHALLENGED

**Evidence check**:
- Sessions cited: `02dd7356`, `0d399262`, `4c64cb3b`, `4249e47a` (Batch 4, Feb 19 21:13-21:18)
- Sessions verified: All four sessions read and confirmed
- Contrast sessions cited: `dfcc2b9e`, `2bec6c42` (Batch 2, Feb 19 18:13-18:15)
- Contrast sessions verified: Both sessions read and confirmed
- Evidence assessment: **Accurate**

All four Batch 4 sessions show:
- `params.max_tokens: 64` present
- `finish_reason: "length"` (not `"stop"`)
- `classification_raw: ""` (empty after regex stripping of incomplete `<think>` blocks)
- `route: "primary"` (default fallback)
- `response_content` contains truncated `<think>` blocks at approximately 64 tokens with no classification word

All contrast sessions (Batch 2) show:
- `params` contains only `{"temperature": 0.0}` (no `max_tokens`)
- `finish_reason: "stop"`
- Full `<think>` reasoning blocks (~200-250 tokens) followed by classification word
- Correct route assignment (`enrich`, `primary`)

Session `4249e47a` ("what is the weather in pdx right now?") is an unambiguous ENRICH query (contains "right now" trigger phrase, asks for real-time weather). Misrouting to primary is confirmed.

Session `4c64cb3b` ("is this the current best research says?") contains "current" (ENRICH trigger word per prompt template), though the CEO correctly notes this is an edge case (qualitative "current" vs. real-time "current").

The CEO's characterization of the technical failure is accurate: `max_tokens: 64` truncates the reasoning model before it emits a classification word, causing 100% classification failure in Batch 4.

**Regression analysis**:

**This proposal targets Python source code, which is outside the Session CEO's authority.** Per `review-board.yaml`, the Session CEO is restricted to `proposable_prompt_files` and `proposable_code_files`. The code whitelist is empty — there are no proposable Python files. This is a **governance violation** that must be CHALLENGED regardless of technical merit.

**However, evaluating the proposal on technical merit alone** (as if it were within scope):

**Positive regression factors** (why this change is likely safe):
- The proposal restores proven working state: Batch 1 (18 sessions, Feb 18 23:29-23:41) and Batch 2 (12 sessions, Feb 19 18:13-18:15) operated without `max_tokens` and produced 100% successful classifications with no runaway generation.
- The model's natural stop behavior is empirically demonstrated: all 30 classified requests in Batches 1+2 show `finish_reason: "stop"` — the model emits `<think>reasoning</think>CLASSIFICATION_WORD` and stops naturally.
- Multiple safety bounds exist: 10-second request timeout at line 126, vLLM server `--max-model-len 32768` ceiling, regex stripping at lines 148-149 handles any length reasoning block.
- The existing decision extraction (lines 150-158) uses substring matching, not exact matching, so trailing text after the classification word is already tolerated.

**Negative regression factors** (risks introduced by this change):
- **Unbounded token generation on malformed queries**: Without `max_tokens`, the classifier could generate far beyond the reasoning block if the prompt template changes or if adversarial input causes the model to fail to emit a stop token. The 10-second timeout provides time-based protection but not token-based protection. If the model generates at ~100 tokens/second (typical for the 8B model), 10 seconds could yield ~1000 tokens of output before timeout — consuming 15x more VRAM/KV cache per request than the current 64-token cap.
- **KV cache pressure during concurrent requests**: The `vllm-router` container has a 14% VRAM budget (~13 GB). Weights consume ~6 GB. The remaining ~7 GB is shared across KV cache (for active requests) and overhead. Batch 2 reasoning blocks were 200-250 tokens. If 5 concurrent classification requests each cache 250 tokens of reasoning at fp8_e4m3 precision, that's 5 * 250 * (model_dim / 8) bytes. For an 8B model with typical ~4096 hidden dim, that's ~640 KB per request or ~3.2 MB total — negligible. But if classification starts generating 1000+ tokens due to a prompt regression or model misbehavior, KV cache consumption scales linearly. This is unlikely to cause OOM (the budget has headroom), but it's a new risk surface.
- **Latency regression for common queries**: Batch 4 classifications completed in 628-651ms (fast, but broken). Batch 2 classifications took 1661-7298ms (2.5x to 11x slower, but correct). Removing `max_tokens` will restore correct behavior but at the cost of slower classification. For a single-user homelab, this is tolerable. But it's a real regression in perceived latency — the broken system *felt* faster.
- **No code-level constraint on reasoning verbosity**: The routing system prompt (line 107, sourced from `ROUTING_SYSTEM_PROMPT`) contains "Reason silently (<30 tokens)." But this is guidance, not enforcement. If the classifier ignores the instruction (model drift, prompt change, adversarial input), there's no fallback constraint. The previous `max_tokens: 64` was attempting to enforce brevity; removing it trusts the model entirely. The CEO is correct that the 64-token cap was too aggressive for a reasoning model, but a higher cap (e.g., 256 or 512) would provide belt-and-suspenders protection without strangling reasoning.

**Alternative not considered by the CEO**: Instead of removing `max_tokens` entirely, increase it to a value that accommodates typical reasoning length with headroom. For example, `max_tokens: 512` would:
- Allow the 200-250 token reasoning blocks observed in Batch 2
- Provide 2x headroom for longer reasoning on ambiguous queries
- Prevent runaway generation beyond 512 tokens (guarded by both `max_tokens` and the 10-second timeout)
- Maintain a token-based ceiling independent of timing

The CEO dismissed this alternative in lines 196-198: "An alternative would be to increase `max_tokens` (e.g., to 256 or 512) rather than removing it entirely. However, this adds complexity for no benefit..." This reasoning is **flawed**. A higher `max_tokens` does not add complexity — it's the same one-line change, just with a different value. And it provides real benefit: defense against unbounded generation. The CEO's argument that "the model naturally stops" is empirically true for the current prompt, but it's not a *guarantee* — prompts change, models change, and safety bounds should not rely solely on model behavior.

**Proportionality check**: The fix is proportional to the problem. The issue affects 100% of classifications. A one-line parameter change is minimal. However, the choice between "remove `max_tokens`" vs. "increase `max_tokens` to 256/512" is significant, and the CEO chose the more aggressive option without sufficient justification.

**Architectural check**:
- ✅ Maintains separation between classifier and generator (no boundary crossing)
- ✅ Changes only the Providers boundary (`providers.py`)
- ✅ Preserves observability (session logs already capture `params` and `finish_reason`)
- ✅ Does not introduce new imports or dependencies
- ⚠️ Removes a safety constraint (token ceiling) without replacing it

**Code-specific checks**:
- **Boundary contract preservation**: The Providers boundary contract states "Classification always returns a valid route name (defaulting to `primary` on failure, with a logged warning)." This proposal does not change that contract — classification still defaults to `primary` on failure. The change affects *when* classification succeeds vs. fails, not the contract itself. ✅ Pass
- **Cross-boundary coupling**: No new imports, function calls, or data flow across boundaries. ✅ Pass
- **Observability impact**: No change to session logging. The existing session JSON already captures `params.max_tokens` and `finish_reason`, so the before/after state is fully observable. ✅ Pass
- **No-Facades Rule**: This change does not introduce any silent degradation or false success. In fact, it **fixes** a No-Facades violation: the current state has classification silently failing (empty `classification_raw`, defaulting to `primary`) with no visible error to the end user. The proposal restores correct classification, making the system appear correct *because it actually is correct*. ✅ Pass (improvement, not violation)
- **Import/dependency changes**: None. ✅ Pass

**Verdict rationale**:

**CHALLENGED on governance grounds**: This proposal targets `src/providers.py`, which is a Python source file. The Session CEO's authority (per `review-board.yaml`) is limited to files in `proposable_prompt_files` and `proposable_code_files`. The `proposable_code_files` list is **empty** — no Python files are within the CEO's edit scope. The CEO explicitly acknowledged this in line 202: "This proposal requires human review because it modifies Python source code, which is outside Session CEO authority."

The boardroom structure is designed with this constraint intentionally. The Session CEO analyzes session logs and proposes prompt improvements. Python code changes require human review because they introduce regression risks that cannot be fully evaluated by log analysis alone. The CEO identifying a code-level issue and documenting the root cause is **excellent work** — it's exactly what the Session CEO should do. But the CEO cannot propose code changes; that proposal must come from a human operator.

**If this were within scope**, I would still CHALLENGE on technical grounds: the proposal removes a safety constraint (token ceiling) without replacing it, and dismisses a safer alternative (`max_tokens: 512`) without adequate justification. The CEO's evidence is strong, the root cause analysis is accurate, and the general direction (remove or raise the token cap) is correct. But the specific choice to remove `max_tokens` entirely, rather than increase it to a value that accommodates reasoning while maintaining a safety ceiling, is insufficiently justified given the regression risks outlined above.

## Cycle Recommendation

**No proposals survived challenge — cycle ends with no changes.**

The CEO has correctly identified a critical code-level bug (`max_tokens: 64` truncating classification reasoning) and provided strong evidence (4 Batch 4 sessions showing failure, 12+ Batch 1+2 sessions showing working state). However:

1. **Governance**: The proposal targets Python source code, which is outside the Session CEO's authority. Code changes require human review and cannot land via the autonomous boardroom cycle.

2. **Human action required**: A human operator should review the CEO report, verify the analysis (which is accurate), and decide whether to:
   - Remove `max_tokens` entirely (the CEO's recommendation)
   - Increase `max_tokens` to 256 or 512 (my recommended alternative)
   - Investigate further before making a change

3. **Boardroom cycle status**: The boardroom has fulfilled its function — the Session CEO identified a critical issue and documented it thoroughly. The Challenger evaluated the proposal and correctly flagged it as out-of-scope. The next step is human review, not QA validation.

**Recommendation for the next boardroom cycle**: After a human operator fixes the `max_tokens` issue (or chooses not to), the Session CEO should review the next batch of sessions to confirm classification is working correctly and evaluate any new issues that emerge. If classification failure persists in the next batch, the CEO should escalate to human review again rather than reproposing the same code change.
