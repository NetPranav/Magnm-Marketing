# Review 20261024-film v2
Verdict: APPROVE

Reviewed: outputs/scripts/20261024-film_script_v2.md (221 lines) against reviews/review_20261024-film_v1.md, using a full line diff of v1 against v2. The diff shows only the changes listed in the v2 change log; everything else is identical to v1, which already passed every other check in the v1 review. The footage facts I verified for v1 still hold: frames 294/295-296/297, 311, 1692, 1694 and 3438, and the crop rectangles in /tmp/reviewer in the cloud workspace.

## Checks (pass/fail + note)
- LazyLayout: PASS. Still absent in every form.
- Accuracy: PASS. "YOUR CALL." replaces "YOUR HAND ON ENTER."; "Wrong name? / It ASKS." replaces "It doesn't guess."; "CANCEL / and it doesn't run." replaces "NO / means no.". Each is scoped to what the footage shows. Section F updated to match.
- Sourcing / claims table: PASS. Rows for shots 8, 11, 12 and 14 are updated; no new claims. The caption and logline match the new wording. The X post is unchanged (206 adjusted chars).
- Tone: PASS.
- Footage truth / timing: PASS. F-Q1 (frame 297, frames 295-296 replaced with 294), F-Q2 (frame 311, interim line kept), F-EXEC 112.10-114.60, F-STOP frame 3438, shot 6 hold of frame 1692, and the 9:16 push rect 432,256,1056,120 all match what I verified. Film times are correct: 16.320, 16.787, 35.000, and the interim line from 16.62.
- Readability: PASS. The "42.5 (3 rows)" lime fill is now an underline. "OR NETWORK" holds for 1.25 s. Shot 3 is corrected to 1.86 s. Max 7 words per card.
- Directive compliance: PASS. Logo once, no voice, varied transitions, pace contrast unchanged. Lime accent list now covers every lime use in the script.
- Burst labels: PASS. Per-beat labels are correct (S-FLOW5 and S-PORT are "real app screenshot").
- Buildability: PASS. Fix 9 is applied in the CMO's version: the zoom goes through the stem of the E, the mask scales, and the footage stays fixed. The CMO's override is correct and my v1 Fix 9 was wrong on that point: the footage shows through the letter strokes, so the O counter would have been ink. The stem version can be built in one pass.
- Photosensitivity: PASS. Unchanged (1.6 inversions per second; the b5-b6 hold removes one swap).
- Spelling: PASS.

## Fixes required (numbered, exact)
None.

## Notes (non-blocking; I will check these in the final render QA)
1. Shot 5 in 9:16: the shot 5 visual says "The footage layer covers the full frame", but section D row 5 says the footage is "scaled 2x" in 9:16. F-HERO's crop at 2x is 1776x1116, which does not cover a 1080x1920 frame. When the E stem fills the frame, the footage would then be a band about 1116 px tall with ink above and below. Either result is acceptable and accurate. Producer: pick one and keep it consistent. Either scale F-HERO to cover the frame (about 3.44x; at that size only the middle of the dialog is visible) for the zoom-through and drop to 2x as the clip-to-box starts, or keep 2x and clip down from the footage band rather than from the full frame. I will check that no hard edge or empty frame appears at 11.625 in the 9:16 render.
2. Shot 5 scale factor: "about 18x / 16x" is an estimate. What matters is the stated condition, "until the stem covers the whole frame". In 9:16 the E stem of roughly 300 px Anton letters is narrow, so check that no ink edge shows at the left or right at 11.625.
3. Section G "Hook" line still says "the first frame is already a readable prompt". The first readable line comes at 2.8 s. This is an internal note only, not posted copy. For X/LinkedIn, set the poster frame to shot 10 ("ASKS FIRST.") or shot 6.
