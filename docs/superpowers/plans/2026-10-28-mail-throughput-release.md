# Measurable mail throughput Windows release

Goal: publish a testable full Windows build including existing OCR reuse/PDF/directory fixes, observable timings, conservative unchanged-mail skipping, and immediate low-water queue recovery.

Architecture: keep existing encrypted database and cursor formats. Store optional read revision/complete markers inside mail payloads. Skip body/attachment download only for a known same ID and binary Outlook change key with complete prior capture; missing/changed markers force normal reading. Persisted pending OCR remains independent. Batch scanned counts include unchanged messages so all-skipped pages advance. A high/low-water scheduler cancels stale timers via generation and schedules quick resume only while automatic reading stays enabled. Performance counters are bounded, in-memory and restart-scoped; durations for concurrent tasks are aggregate work time, not elapsed wall time.

- [x] Add performance recorder with deterministic tests and bounded recent-rate window; integrate read/download/analysis/cache/save timings and counters into status and exportable UI.
- [x] Add failing tests for repeated known mail avoiding body/attachment access, changed/unavailable revisions and incomplete captures forcing reread, and all-skipped cursor progression. Implement callback plumbing without bypassing capture of new content.
- [x] Add high/low-water tests including cancellation on stop, stale timer rejection and unchanged cursor. Integrate completion-triggered resume, preserving disk/storage guards.
- [x] Expand frozen Windows smoke coverage for statistics and restart preservation/reuse; run full backend suite, frontend build and mocked browser verification.
- [ ] Review, commit only intended source/tests/docs, push and dispatch full Windows workflow. Inspect run output and provide the successful artifact link; fix build failures before delivery.

Release isolation: created a managed worktree at base 8f44ae97 because another user task began an unrelated blob/preview/maintenance migration in the original workspace. Only the intended throughput fixes are in this candidate. Reviewer found a canonical self-match revision marker refresh issue; fixed with a third-scan regression preserving completed evidence.
