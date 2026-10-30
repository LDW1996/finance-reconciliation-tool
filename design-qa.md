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
- Chinese full edition retains the existing management shell and iLog retention controls. Standalone English edition has mail settings only. Paths can contain original Chinese characters and are never translated.

## Functional checks
- English save succeeded with the existing API and success message.
- Optimization preview showed running state, disabled conflicting controls and then enabled confirmation only when preview completed. No optimization execution or deletion was performed.
- Conservative cleanup preview remained available; no destructive action performed.
- Section jump preserved the current route hash.
- Desktop and 390px mobile document bounds checked; no horizontal page overflow or offscreen form controls.
- Chinese full edition iLog retention section and its controls remain present.
- Chinese browser console checked: no errors reported.
- Both production frontend builds and strict English catalog/runtime business-data preservation checks passed.
- Compared the existing async load/save/preview/cleanup/restore/poll/unmount function source to HEAD: unchanged.

## Residual limits
- This is frontend layout work. No Windows executable was rebuilt or validated in this pass.
- Destructive cleanup and restore against real user data were not run; their existing handlers and safeguards remain unchanged.
- P3: shorter optional English labels could make the six-column metadata strip denser, but current full labels are readable and do not clip.
