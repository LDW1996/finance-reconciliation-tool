# Unified local platform acceptance

Implemented one release entry point with four built-in local capability accounts, runtime Chinese/English selection, module-scoped settings, and backend route enforcement. Plaintext account delivery files remain in ignored `build/platform-credentials/`; only salted password hashes are packaged.

## Local evidence

- Full backend suite: 428 passed, 2 skipped after final lifecycle review fixes.
- Review found and fixed failed-logout suspension and iLog acquisition races; regression tests include real-manager lock reentrancy.
- Vue/TypeScript production build passed.
- Runtime locale source isolation tests passed.
- Real local HTTP login/logout and allowed/denied mail, iLog, Excel routes verified for all four delivered accounts.
- Browser verified full navigation and mail-only English settings. Browser caught a frontend fallback shadowing the session route; fixed route ordering and added a regression test.
- Representative mail fixtures verify original key, cursor, completed recognition and pending queue retention. This is not verification against the user's complete Windows production database.

## Release prerequisite

The Windows frozen executable must pass the new normal-login role matrix, offline OCR, business probes and restart/key preservation checks in Actions. The workflow requires the repository secret `PLATFORM_SMOKE_ACCOUNTS` containing the four delivery passwords as a JSON object. The available GitHub credential returns 403 for Actions Secrets; the browser settings page is inaccessible. User authorization was received, but upload has not succeeded. The ignored local JSON file is prepared for manual entry. No Windows frozen-executable pass is claimed.

## Upgrade behavior

Existing data paths, database format, encryption key and scan cursors are retained. Program restart requires login; mail-capable login then applies existing recovery preferences. Logging out does not erase business progress. No real user production data was modified during this acceptance.
