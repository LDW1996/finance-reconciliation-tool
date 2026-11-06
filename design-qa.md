# Retention settings redesign QA

final result: passed

## Source and implementation
- Selected visual: `output/retention-redesign/selected-design.png` (first displayed concept, 1484 × 1060).
- English implementation: `http://127.0.0.1:8769/`, isolated demonstration data; `output/retention-redesign/english-final.png`.
- Chinese implementation: `http://127.0.0.1:8765/#retention`, separate local preview data; `output/retention-redesign/chinese-final.png`.
- Browser: Codex in-app browser. Desktop CSS viewport 1440 × 1024; mobile 390 × 844.
- English full-page capture 1425 × 1555; Chinese full-page capture 1425 × 2039. Vertical scrollbar excludes 15 CSS px. Screenshots normalized by content width for comparison; full-page images retain naturally scrolling content.
- Combined comparison: `output/retention-redesign/comparison-final.png`; both reference and implementation were opened together. Full-page captures inspected for lower content; separate tight region crops were unnecessary because the combined desktop comparison and full captures expose all regions.

## Comparison history
- Initial P2: redundant standalone title plus component title displaced the usage overview. Fixed by using one standalone h1 and placing the section navigation above the overview.
- Initial P2: long English explanations and vertically stacked field labels made the rules area substantially taller than the selected design. Fixed with two-column inline desktop form rows, naturally wrapping labels, and expandable detailed operating instructions while retaining visible protection notices. Rebuilt and recaptured the final desktop page.
- Post-fix comparison: selected overview → grouped secondary metrics → two-by-two rules with adjacent save → optimization → conservative cleanup → diagnostics hierarchy retained. No remaining actionable P0/P1/P2 layout findings.

## Fidelity and intentional constraints
- Typography: existing system sans-serif retained; 14px readable explanatory text, 18–22px section titles, 26–30px primary metrics. Full English labels wrap instead of clipping.
- Layout: flat white settings surface with subtle tinted metrics/rules bands, fine dividers, restrained radii, no nested cards. Desktop and mobile controls remain within the viewport.
- Tokens: existing application blue, gray and white retained, including current shared primary button token. No unrelated product-wide palette changes.
- Assets: no raster artwork is required. Existing product icon library and functional progress bar retained; no invented illustrations.
- Content: kept existing units, original data and server-driven status. Removed inaccurate AI mock copy, unsupported standalone backup/delete/copy buttons and invented automatic-cleanup behavior. Detailed instructions remain available through disclosures. This intentionally yields more scrolling than the concept, rather than removing existing protections or workflows.
- Source demo percentage is rounded 46%; fixture's exact bytes yield 45% while displaying 9.10GB. Production percentage still uses the unchanged byte-based calculation; no percentage was hardcoded.
- The unified platform retains the existing management shell and iLog retention controls in both language modes. Paths can contain original Chinese characters and are never translated.

## Functional checks
- English save succeeded with the existing API and success message.
- Optimization preview showed running state, disabled conflicting controls and then enabled confirmation only when preview completed. No optimization execution or deletion was performed.
- Conservative cleanup preview remained available; no destructive action performed.
- Section jump preserved the current route hash.
- Desktop and 390px mobile document bounds checked; no horizontal page overflow or offscreen form controls.
- The iLog retention section and its controls remain present in the unified platform.
- Chinese browser console checked: no errors reported.
- The production frontend build and strict English catalog/runtime business-data preservation checks passed.
- Compared the existing async load/save/preview/cleanup/restore/poll/unmount function source to HEAD: unchanged.

## Residual limits
- This is frontend layout work. No Windows executable was rebuilt or validated in this pass.
- Destructive cleanup and restore against real user data were not run; their existing handlers and safeguards remain unchanged.
- P3: shorter optional English labels could make the six-column metadata strip denser, but current full labels are readable and do not clip.

---

# Login redesign — 2026-11-02

- Source truth: `/Users/hujiarong/.codex/generated_images/01a18167-8b7b-7ab3-9365-6fe86ad1135e/exec-032ed8ce-205c-4b6b-8a88-d552e0fe05a5.png` (first displayed, selected concept).
- Implementation: `http://localhost:8772/`, isolated `build/login-preview-data`.
- Source dimensions: 1487 × 1058. Desktop CSS viewport 1487 × 1058, DPR 1; full-page capture 1487 × 1058. The initial viewport-only screenshot was clipped by browser capture and was excluded from comparison. No stretching used in final evidence.
- Full-view comparison: `build/login-qa/comparison-final.png`; implementation: `build/login-qa/desktop-zh-final.png`, final handoff: `build/login-qa/desktop-handoff.png`.
- Additional captures: `build/login-qa/desktop-en.png`, `build/login-qa/mobile-en.png` (390 px wide, naturally scrolling full-page error state).
- Focused separate crops not needed: the combined full view clearly exposes brand, form and footer regions; DOM measurements were used to check exact form boundaries and overflow.
- Iteration 1: form started too low and heading/field spacing differed from selected reference. Result blocked for layout adjustment. Increased heading scale, aligned the form horizontally and vertically, adjusted field rhythm.
- Iteration 2: combined source/render capture confirms the selected half-width navy photograph/white form composition, aligned title/fields/button and footer. No actionable P0/P1/P2 visual issue remains. P3: generated paper still-life differs slightly in crop/lighting from the mock; all UI text remains live, not flattened into the image.
- Interaction checks: Chinese/English toggles; language preserved on successful login into workspace; required fields; password show/hide; generic wrong-credential feedback in English; real mail-role login; logout back to clean login; no horizontal overflow at 390 px.
- Console errors checked after the interactions: none.
- Build and language source-isolation checks pass. Auth/permissions/runtime regression tests: 15 passed. No backend authorization or mail data migration changed for the login redesign.

final result: passed

---

# Unified platform header cleanup — 2026-11-02

Source issue: user screenshot `/var/folders/2g/1vxq42vj6035d64_rqy2_jkc0000gn/T/codex-clipboard-fbb629ce-a05e-40a9-8aea-335dab266e86.png` showed equal visual weight for brand/nav/account, duplicated `excel · excel`, left-clustered logout and oversized active navigation. This was treated as a scoped header correction, preserving page content and all existing destinations.

- Brand left, compact module navigation middle, account/exit right; identity appears once.
- Desktop evidence: `build/header-qa/full-zh.png`, focused `build/header-qa/full-header.png`. English and all role evidence: `build/header-qa/full-en.png`, `excel-en.png`, `mail-en.png`, `ilog-en.png`.
- Responsive evidence: `build/header-qa/mobile-en.png`, focused `mobile-header.png`, 390 × 844 CSS viewport. Navigation scrolls within its own second row; account and exit stay in the first row.
- At 1440 px full-English navigation, measured nav scrollWidth equals clientWidth after English-label spacing fix; document has no horizontal overflow at desktop.
- Real logins verified excel/mail/ilog/full each show only their own permitted links. Selected-page semantics retained with `aria-current`; keyboard focus outlines retained.
- No language switch is repeated inside the platform. Login preference still controls the complete interface. User guide corrected accordingly.
- Production build, locale source-isolation and whitespace validation pass. No business state or authorization policy changes.

final result: passed

## Numeric control seam — 2026-11-02

Cause reproduced in the browser: the shared input wrapper was 40 px high, while Element Plus right-hand stepper controls remained fixed at 15 px each, leaving an 8 px dead gap. The old Excel-only override did not cover mail storage, iLog retention or Outlook settings. Promoted the actual-container half-height rule to shared `.el-input-number.is-controls-right` styling.

Browser validation in isolated data: six storage/retention fields, Excel tolerance, and both mail date/batch fields. Desktop controls 40 px with two 19 px halves and zero gap; narrow controls 44 px with two 21 px halves and zero gap. Increase/decrease returned a temporary form value 10 -> 11 -> 10 without saving any settings. Disabled mail controls also retain the correct geometry. Build and whitespace checks pass.
