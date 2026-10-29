# Standalone English Mail Reconciliation edition

User request: extract the mail receipt reconciliation feature into an independent English Windows package.

Scope:
- Dedicated frontend shell with Mail Reconciliation and Mail Storage only; English static UI and Element Plus controls. No Excel reconciliation, iLog unlock or account management navigation.
- Dedicated backend/launcher exposes mail APIs and health only. English generated interface messages and reports; original subjects, bodies, customers, payer names, workbook data and OCR evidence stay in their source language. Full Chinese and standalone Excel editions remain unchanged.
- Reuse existing mail data directory/key/cursors for same-user upgrades, do not migrate/reset data. Same8765 port ensures mutually exclusive normal launches. Build files never include user data or service credentials.
- Keep Outlook, MSG/EML import, OCR/PDF, bank and customer online sources, mapping previews, review/export/writeback, storage management and diagnostics.
- Dedicated PyInstaller spec excluding unrelated services and Playwright. English launcher, bundle probe, smoke test and guide. Add mail_en workflow edition and an automatically triggered English-package workflow so the requested EXE can be produced without API dispatch access.

Execution: frontend localization and backend/package work in separate file scopes; root owns workflows, English guide, integration verification and final build monitoring. Validate API isolation/English generation without modifying Chinese user data, frontend string coverage, build separation, offline OCR/PDF/MSG runtime, key/queue continuity, and English browser flows. Run relevant regression suites and Windows smoke tests; only a completed Windows run confirms a downloadable package.
