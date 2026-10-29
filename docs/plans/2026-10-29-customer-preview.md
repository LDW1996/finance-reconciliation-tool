# Customer mapping snapshot preview

Approved: add a read-only, searchable, paged preview of the active local customer snapshot. Separate valid mappings and excluded rows, show original sheet/row, raw relevant fields, concrete exclusion reasons and shared-email/one-to-many flags. Never fetch Google or modify mapping/matching/reviews when previewing.

- Capture invalid and duplicate row diagnostics during future imports, encrypted beside current entries. Keep matching revision/accepted rows and old skipped-count semantics unchanged.
- Exclude diagnostic arrays from summary APIs. Preview endpoint returns at most100 rows, status and counts, source metadata; protect with existing local access guard.
- Old snapshots retain usable valid entries. If excluded diagnostics were not saved, explicitly say re-reading is required; never invent old rejected rows or automatically fetch.
- Add preview dialog with valid/excluded tabs, search and pagination. Show snapshot source/time and participation state. Cancel stale requests, contain errors inside dialog.
- Validate parsing, encrypted persistence, original source rows, legacy fallback, filtering/paging/shared flags, read-only API, browser tabs/search/mobile. Build, restart local app and push main.
