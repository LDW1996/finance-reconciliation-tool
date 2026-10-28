# Attachment recognition reuse and text PDF fast path

Goal: avoid duplicate OCR across mails and avoid raster OCR for complete text pages with small decorative images, preserving stored evidence and review state.

- [x] Add failing tests for concurrent identical attachments, persistent reuse, independent evidence IDs, changed bytes/type, failed-result retry, and old completed attachments.
- [x] Add a versioned derived recognition lookup referencing encrypted attachment results, without duplicating previews or altering legacy results. Backfill once per version. Use bounded striped locks around lookup/analysis/save to prevent duplicate in-flight work. Clear derived lookup with local mail clear; validate every lookup against current attachment metadata.
- [x] Add PDF fixtures for small logos, large scans, mixed pages, sparse/invalid text. Small images require a cropped text audit; any text or uncertainty retains full OCR. Include a real OCR conflict test for image amount 900 versus native amount 1,400,000.
- [x] Extract text before rendering. Use preview-sized rendering for native text pages; retain existing scan rendering and evidence shape.
- [x] Run focused tests, whole backend suite and review. Retain unrelated directory-selection UI fix without bundling it into this change.

Review: corrected the initial size-only image heuristic after a reviewer reproduced a missed correction amount. Final crop-audit behavior reviewed with no remaining blockers. Old-result lookup preparation runs in the background coordinator.
