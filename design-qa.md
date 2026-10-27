# 自动收取邮件布局验收

source visual truth path: /Users/hujiarong/.codex/generated_images/01a029f6-88cd-72c1-8145-50e2e0996338/exec-1d51df54-bef5-4753-a14b-774bf0d83b42.png
implementation screenshot path: output/design/mail-settings-desktop.png
viewport: 1440 × 1100 CSS px; narrow check 320 × 900 CSS px
source dimensions: 1798 × 875 image pixels; nominal 1440-wide desktop component.
implementation dimensions: 1361 × approximately 671 image pixels, component crop, screenshot scale 1. Compared proportional component regions, not application chrome. Images opened together in the same comparison input.
state: recent 90 days, batch 100, 2 selected mail folders, include children enabled, timer disabled. Browser uses an isolated temporary local fixture to simulate Windows folder availability; it never reads or modifies an actual Outlook mailbox. Actual Mac page independently checked for disabled connection controls and clear compatibility text.

## Fidelity surfaces
- Typography: existing system Chinese font retained, section heading 17px, title 18px, body 14px and subordinate help 13px. No clipped primary labels. More compact than generated mock to remain consistent with the existing application.
- Layout: approximately 55/45 split, searchable directory tree on left, settings and scheduling on right, one footer for progress and action buttons. At 320px the columns stack and document scroll width stays 305px (viewport 320px), including fixed-date mode.
- Colors: existing blue/white/gray tokens retained. Recent/fixed segmented selection now uses light blue rather than a heavy solid fill.
- Assets: standard Element Plus folder/search/clock/play/refresh icons, no new raster asset required. Mock's decorative Windows logo intentionally omitted; actual platform is stated in text.
- Copy: mocked account replaced with a generic test mailbox. No fabricated 'connected' claim. Parent folders are not automatically checked when only children are selected, consistent with strict independent directory selection. Long read-only/data-coverage notes folded below the footer.

## Comparison history
1. Initial browser comparison found P2: selected date segment inherited heavy solid fill inconsistent with the reference. Fixed selected inner background/text/border tokens; recaptured and compared with the source. Title size increased to restore hierarchy.
2. Final paired comparison: no remaining P0/P1/P2 layout issues. P3: native library tree icons and checkboxes are smaller than the concept image, retained for existing application consistency.

## Interaction checks
- Clicking directory label adds/removes selection and updates count 2→3→2.
- Searching 巴淡 hides unrelated folders without losing selection.
- Switching specified dates shows range input and disables scan when dates are empty.
- Mobile specified-date mode has no horizontal document overflow.
- Browser console error log: empty on the isolated fixture.
- Frontend type checking and production build passed; existing large-chunk advisory remains.
- Real Outlook/COM integration remains a Windows acceptance check, not covered by this visual review.

final result: passed
