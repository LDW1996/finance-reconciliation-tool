# Unified receipt workspace implementation plan

> Execute inline, preserving existing audit edits. User has approved the complete design and implementation; no additional approval gate or automatic commit/push.

**Goal:** One matching generation shared by receipt views, mail reverse lookup, export and writeback; implement the approved compact receipt workspace and source settings.
**Architecture:** An encrypted, rebuildable per-receipt result index with one worker per workspace and generation checks. Lightweight state polling never ranks; view-only filters read the same index. Existing synchronous interfaces remain compatible for exports and confirmation validation. No mail/review/scan schema migration or deletion.
**Tech:** Python/SQLite/FastAPI, Vue3/Element Plus, existing locale tooling.
**Spec:** six approved design frames in this conversation (main list, source settings, preview, review drawer, states, auxiliary operations).

## Constraints
- Preserve original mails, payments, confirmed reviews and scan checkpoints.
- Source synchronization requires explicit action; only stored payment changes trigger local match refresh.
- Reject stale confirmations/writeback; never replace a successful snapshot with a failed/incomplete generation.
- Keep encrypted derived results bounded on disk, not all candidates resident in memory.
- English/Chinese supported. No new external service, no real online write or cleanup during validation.

## Review focus
- Data changes while computing: discard stale generation; preserve last success.
- Concurrent list/reverse/export: share one generation, no duplicate rank calls.
- Candidate beyond the first three: reverse lookup and confirmed evidence remain discoverable.
- Failure/restart: show actionable error, old results survive; retry explicit after failure.
- Hidden page and request failure: polling is light, no repeated heavy query or stuck button.

## Tasks
- [x] Add failing behavior tests for shared generation across scope/limit/reverse/filter, invalidation, concurrent requests and failure.
- [x] Implement encrypted result index and generation coordinator; adapt workspace and reverse lookup; asynchronous snapshot/state API; preserve sync export/writeback validation.
- [x] Implement compact main list, review tabs, more filters/actions, clear update states; reduce parent polling work while matching tab active.
- [x] Implement source settings dialog, separate configs/reads, coordinated update, bank preview, existing customer preview and backup import.
- [x] Verify affected tests, full backend suite, frontend build/locale checks and browser main/settings/drawer/error/mobile; document findings and limitations.

## Acceptance limits
- Backend suite: 560 passed, 2 skipped. Frontend and English catalog checked.
- Browser main, source settings, bank preview, confirmed evidence drawer, isolated update failure, and 390px source dialog checked using local copied data.
- Full-product visual audit and Windows Outlook/end-to-end packaging remain outside completed acceptance. No real Google writeback performed.
- User requested switching to the formal 8765 service; restarted against its original persistent directory, with no deletion or reset.
