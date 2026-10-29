# Customer mapping for mail matching

Approved scope: map bank payer (person or company) to customer and sender email to improve candidate ranking, without replacing original payer or automatic confirmation.

- Add encrypted local XLSX/CSV contact-ledger import, optional sheet selection, status and enable/disable. Validate before replacement; retain previous ledger on error. Separate from bank receipts.
- Exact normalized payer alias and full email only. Preserve one-to-many relations. Stronger direct payer matches retain precedence. Mapping applies only to equal known currency and exact amount within existing date window; shared email/bank relationships score lower. No domain or display-name-only guess.
- Return mapping score and source rows, append explanation to candidate remarks/evidence so exports and review signatures track changed evidence. Do not mutate OCR/payment records, mail IDs, queue or cursors. Cache invalidates on mapping revision.
- Apply same policy to ranking, confirmation revalidation, reverse lookup and exports. Preserve existing confirmations when their evidence is unchanged.
- Add import and candidate-evidence UI following existing components. No online contact-sheet access or credential changes; user imports exported workbook.
- Validate with regression tests for missing payer, explicit alias, shared mailboxes, direct precedence, differing currencies/amounts/dates, grouped source copies, stale confirmation, disabled/absent mapping, encrypted persistence and invalid imports; frontend build and browser probes.
