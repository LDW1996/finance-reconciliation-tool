# Bounded four-process OCR

Goal: up to four concurrent mail analyses, keeping financial evidence unchanged.

Design: one queue coordinator owns claims and acknowledgements. At most four mail tasks run in controller threads; attachment parsing runs in independent spawned processes, each with one OCR compute thread. Only the parent writes SQLite. Injected analyzers remain in-process for existing tests. Pool lifetime is one backlog drain, so idle workers exit. Startup available-memory and CPU limits may reduce concurrency; FINANCE_OCR_WORKERS=1..4 permits a lower ceiling. Unknown available memory defaults to one worker. Retain existing durable recovery and per-attachment failure behavior. Windows launcher must call freeze_support before normal startup.

Implementation:
- [x] Add failing tests for four overlapping tasks, bounded dispatch, duplicate enqueue, failure continuation, resource limits and spawn execution.
- [x] Add resource-policy and spawned executor module; implement coordinator with bounded submissions and active mail identities.
- [x] Limit OCR/OpenCV native threading in process initializer; add frozen launcher support.
- [x] Run concurrency, attachment, queue and mail task tests, then backend suite. Document limits and Windows verification gap.
