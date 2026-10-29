# Two independent online import sources

Approved design: top-level Bank receipts and Customer contacts cards, each with independent Google Sheets URL/gid, config, manual read, active record count and last successful read. Customer table is strictly read-only. Borrow bank authorization or use separate encrypted credentials. Reuse last valid data on network/format/permission errors. Local imports live in a single collapsed backup section; bank writeback/history live beside results.

Implementation:
- Add read-only Sheets client mode (read-only OAuth scope, forbid writes, no formula fetch) and an online customer adapter using the existing parser and encrypted mapping state.
- Customer configuration and data are separate from bank settings. Config changes do not replace active data. Validate whole snapshot before an atomic mapping+last-read update. Failures only persist error status. Network reads do not hold the matching lock; reject stale replacement if another mapping change occurs during the read.
- Add config/read endpoints and service methods. Keep existing file endpoints and bank writeback unchanged.
- Refactor source layout to two cards and customer config dialog. Preserve local imports below results, mapping enable/disable and manual refresh semantics.
- Test independent sources and credentials, read-only scope/write guard, failed/invalid refresh retention, atomic rollback, stale refresh protection, API access and browser flows on desktop/mobile. Rebuild and restart local app after verification; push main.
