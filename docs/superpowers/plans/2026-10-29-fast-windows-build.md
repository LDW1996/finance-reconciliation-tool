# Fast Windows Build Implementation Plan

**Goal:** Avoid repeating Windows tests for an already validated commit while retaining executable smoke checks.

**Architecture:** Add an independent Windows Tests workflow for pushes to main, pull requests and manual runs. Build Windows EXE defaults to its existing full checks. An explicit fast_build input permits skipping tests only if the same SHA has a successful trusted push/manual Windows Tests run. Missing, running, cancelled, failed or unrelated runs never qualify. Smoke tests remain mandatory before artifact upload.

**Tech Stack:** GitHub Actions, GitHub REST through github-script, offline Node contract tests.

- [x] Add regression checks for exact-SHA validation, failed/untrusted runs, safe default and unskippable EXE smoke check.
- [x] Implement Windows Tests workflow and the fast-build eligibility gate before dependency installation.
- [x] Document full and fast build choices and actual limits of saved time.
- [x] Validate YAML, run focused offline checks, existing backend tests, then commit/push main. Frontend source and runtime are unchanged; a frontend rebuild is not needed for this workflow-only change.

No runtime application or saved mail data changes. Live workflow dispatch may require a manual click because the current API token lacks dispatch permission.
