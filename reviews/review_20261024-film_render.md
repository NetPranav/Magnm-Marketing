# Review 20261024-film render (final QA)
Verdict: REJECT (4 required fixes. All are layout/compositing; there are no content or claim problems. One re-render of both ratios is needed.)

Files checked (cloud workspace): /home/claude/film/out/cero_see-it-before-it-runs_16x9_web.mp4 and _9x16_web.mp4. Both are H.264 at 30 fps, 1920x1080 and 1080x1920, 1407 frames (46.9 s), AAC 48 kHz stereo.
Method: I made contact sheets at 1 fps for both ratios and looked at all of them. I sampled the burst at every 8th note, sampled shot 5 at 30 fps from 10.95 to 11.45 s plus stills to 11.7 s, and checked exact frames at every sync point. I also looked at the last frame of each file and both thumbnails. Audio was checked with ebur128, astats and windowed RMS. Frames are in /tmp/reviewer/r/. Checked against script v2 (outputs/scripts/20261024-film_script_v2.md) and reviews/review_20261024-film_v2.md.

## Checks (pass/fail + note)
- Content / claims: PASS. Every on-screen line matches script v2 exactly in both ratios ("YOUR CALL.", "Wrong name? / It ASKS.", "CANCEL / and it doesn't run.", the burst sentence, "ASKS FIRST.", the end lines). No LazyLayout, no OS names, no repo link.
- Crops: PASS. The "pranav@Pranavs-MacBook" prompt and the status bar never appear in either ratio. "/Users/pranav" appears only inside command strings, which was accepted in v1.
- Footage timing: PASS.
  - Answer: frame 504 (16.800) is the first frame with "✓ The total ... 42.5 (3 rows)."; frame 503 shows the interim line.
  - Cancel: frame 899 shows the dialog, frame 900 (30.000) shows "Declined in the confirmation dialog.".
  - Stopped: frame 1049 shows the running state with cursor, frame 1050 (35.000) shows "Stopped." with "means STOP.".
  - These are identical in 9:16. Frames 295-296 (the unrelated ghost text) are correctly removed (film frame 489 shows the empty input).
- Request line: see note 1. The line is complete at frame 491 (16.367), not 16.320. This was my v1 error, and it is harmless.
- Shot 5 zoom-through (my v2 notes 1 and 2): PASS. In 9:16 the producer chose the band option: ink above and below the footage band, no empty frame, no stray hard edge. The band fills the frame width by about 11.45 s and clips to the box at 11.7 s. The zoom goes through the E stem as specified.
- Readability holds: PASS except where noted below. URL on screen 45.625-46.9 (1.27 s). End line fully typed by about 42.3 s in 9:16 (about 1.4 s hold).
- Text clipping: FAIL (Fixes 1 and 2).
- Logo once: PASS. Magnm logo only on the end card (multiply on stone), in both ratios.
- Labels: PASS in 16:9 (correct per beat: footage/screenshot). In 9:16 the labels overlap the dialogs (Fix 2).
- End card: PASS in 16:9. The last frame of both files is the full end card (no fade to black). The 9:16 layout deviates from the script (Fix 4).
- Thumbnails: PASS. thumb_16x9.jpg and thumb_9x16.jpg are "ASKS FIRST.", as recommended.
- Audio: PASS.
  - Integrated loudness -14.6 LUFS, LRA 9.8 LU.
  - True peak -1.0 dBFS; astats sample peak -1.03 dB with 2 peak samples, so no clipping.
  - Both files have the same audio track (PCM md5 match).
  - Silence: the music cuts at 35.000 with a short (about 50 ms) tail, so no click. From 35.05 to 37.20 it stays below -48 dB (35.1-37.2 measures -79 dB RMS). The paper-fold sound starts at 37.25 as scripted, and the pad enters at 37.5.
  - The held breath (8.2-9.3) is -46.5 dB room tone.

## Fixes required (ranked by the directive's priority order: 1 misleading info - none; 2 broken scenes - none; 3 readability; 4 visual consistency)

1. [Readability: clipped text] 16:9 shot 11, 28.750-30.000 (frames 863-899): the F-HERO box covers the "L", so the word reads "CANCE" for 1.25 s. CANCEL runs from about x 124 to 930. The box currently spans x 823 to 1800.
   Fix: for 28.750-30.000 only, scale the F-HERO box to 0.92x (816x513) and set its left edge at x 980 (right edge 1796, inside the 120 px gutter). Keep it vertically centred on CANCEL as now. This leaves at least a 48 px gap between the "L" and the box. From 30.000 on, the layout is fine as rendered.

2. [Readability: clipped type and labels] Burst, shot 9 (23.125-26.875):
   (a) 16:9: the dialog box sits on top of the type's bottom edge. On b1 the box top is at y 358 while the ANYTHING baseline is about y 372, so the bottoms of T-H-I-N-G are cut. On b3 the box covers the comma and part of the "S" in "YOUR FILES,".
   Fix: for all six beats in 16:9, set the box top edge at y 412 or lower (at least 40 px below the type baseline) and scale each dialog to fit between y 412 and 1000 (588 px tall max, about 0.92x for F-HERO). Type stays where it is and is always above the box in z-order.
   (b) 9:16: the per-beat footage label sits on the dialog's lower-left corner (b2, b4, b6). On b3 (stone ground) the box covers part of it, so it reads "real a[box] screenshot".
   Fix: place the label 16 px below the bottom edge of each dialog box, left-aligned with the box, top of the z-order, muted #6B6B66 on stone or stone-muted on ink.

3. [Readability: product name] Shot 5, 9.375-10.625, both ratios: the CERO letters show the dark dialog (about #1F1F22) on ink #0A0A0A, roughly 1.2:1 contrast, so the product name reads only by its silhouette. On a phone at 9:16 it is barely there. The script (shot 5 visual) specifies "stone outline 2 px", but it is missing from the render.
   Fix: add a 2 px stone (#EFEFEC) stroke around the CERO letter mask from 9.375 to 10.625, at a constant 2 px (non-scaling) during the 100->106% push. Remove it with a hard cut at 10.625, when the zoom-through starts.

4. [Visual consistency: spec deviation] 9:16 end card, 43.750-46.875: the stack is left-aligned at about x 105 with the right half empty, and the logo is about 255 px wide. Script v2 section D row 16 says: "same stack, centred; logo width 360 px".
   Fix: centre CERO, the "by + logo" pair, and the URL on x 540, and set the logo to 360 px wide. Keep the vertical positions. The 16:9 end card is correct as rendered.

## Notes (non-blocking)
1. Request-line frame: my v1 review said source frame 297 (9.900) is the complete line. That was off by one frame. Frame 297 still shows the request mid-entry ("...score co" with the app's grey completion of the same text), and the full line is source frame 298 (9.933) = film 16.367 (frame 491). The single partial frame (film frame 490) shows the same request text, not unrelated text, so no fix is needed. Optionally move the Enter keypress from 16.320 to 16.367.
2. The paper-fold sound at 37.25-37.5 measures about -18 dB RMS on a mono downmix, louder than the pad that follows (-30). The script calls it "very soft". Consider lowering it by about 8 dB. It is not a clipping or level problem.
3. "OR NETWORK" swaps style again at 26.5625 (script: one swap at 25.9375 only). This is harmless; fix only if the burst is re-rendered anyway (it will be, for Fix 2).
4. In shot 12 the "Stopped." strip sits directly under F-EXEC with no 24 px gap. It reads naturally because it is the real terminal layout; leave it.
5. After the fixes, re-check only these frames: 16:9 shot 11 (28.75-30.0); the burst in both ratios at each 8th note; shot 5 at 9.4-10.6 in both ratios; and the 9:16 end card. Everything else can stay as rendered.
