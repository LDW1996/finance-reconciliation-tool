# Conservative local mail cleanup

- Separate unmatched mail from mail with no detected financial clues. Neither is proof of uselessness.
- Initial deletion candidates must be fully read, indexed empty, have no attachments, warnings, candidates, financial/business clues or review references. Protect unknown formats and decoding failures.
- Preview at most 200 candidates per batch, unchecked by default; user reviews and explicitly chooses each mail.
- Revalidate encrypted snapshot and eligibility inside a single immediate transaction. Back up exact encrypted database records to a compressed encrypted file before any deletion. Failure rolls back the batch.
- Preserve Outlook originals, folder cursors and all attachments. Existing deduplication handles attachment space savings. Do not create permanent scan tombstones.
- Allow restoration without overwriting newer mail. State that backups occupy disk, cleanup does not immediately shrink SQLite, and mail can be imported again.
- Verify payment clues, incomplete capture, attachments, stale preview, backup failure, review protection, restore conflicts and API confirmation.
