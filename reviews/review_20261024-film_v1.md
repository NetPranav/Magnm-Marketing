# Review 20261024-film v1
Verdict: REJECT (9 required fixes, all small text/timing edits; no structural rewrite needed. Re-review after v2.)

Reviewed: outputs/scripts/20261024-film_script_v1.md (all 207 lines) against briefs/BRIEF-20261024-film_reviewer-v1.md, agents/reviewer.md, research/product-facts.md, outputs/research/20261024-film.md, outputs/strategy/20261024-film.md, playbooks/video-directive.md, research/reference-style.md, video-skills/magnm-video/references/taste-for-video.md, video-skills/magnm-video/references/magnm-brand.md, briefs/BRIEF-20261024-film_writer.md (CMO decisions 1-7).
Footage: verified by the Reviewer in the cloud workspace with ffmpeg + frame-by-frame viewing of /home/claude/repos/Cero-Terminal/docs/media/cero-demo.mp4 (1920x1080, 30 fps, 147.5 s, start_time 0) and docs/images/close-port.png, flow-approval.png. Every crop rectangle in C.0 was extracted and looked at. Frame numbers below = src_seconds x 30.

## Checks (pass/fail + note)
- LazyLayout: PASS. Not named, shown, or hinted. No "something else is coming". Ref 3 index tree explicitly excluded.
- Accuracy: FAIL (3 lines). "YOUR HAND ON ENTER." is misleading (Fix 1). "It doesn't guess." is an absolute the product contradicts (Fix 2). Everything else matches product-facts / README wording; the burst sentence is the approved scoped wording ("Anything that changes your files, system or network asks first."), and "Questions just get answered" is backed by footage (verified: no dialog at any frame from src 9.8 to 12.9).
- Sourcing / claims table: PASS with the edits in Fixes 1-3. No stats, OS names, competitor names, model sizes, "100% local", "safe/secure", or repo link on screen.
- Tone: FAIL (1 line). "NO / means no." borrows the consent-campaign slogan (Fix 3).
- Footage truth: PASS with timing corrections (Fixes 5-7). All crops show what C.0 says. The prompt "pranav@Pranavs-MacBook" and the status bar are excluded by every crop (verified). The declined folder is never shown opening: F-DECL shows "✗ Could not open ~/cero-demo/Projects/gitbrains: Declined in the confirmation dialog." (red ✗, confirmed). Cancel frame confirmed: dialog last visible frame 1693, declined line first visible frame 1694 = src 56.467. No demo title slides used; F-DECL (to 58.97) and F-STOP (to 116.48) stay inside app footage (verified).
- "/Users/pranav/..." paths: ACCEPTABLE, keep. CMO decision 4 bans the prompt string and status bar only; the command string is the proof the film is about; "pranav" is the CEO's first name already public as repo owner NetPranav. No swap needed. (Swap plan in C.0 remains valid if the CEO objects.)
- Readability: PASS with Fix 4. Every key line holds >= 1.2 s (shortest: URL 1.25 s; shot 3 Anton is 1.86 s not 2.19 s, because it exits at 7.800 - still fine). Max 7 words per card (shot 15 line 2; shot 3 card). Burst: 1-2 words per 0.625 s beat, type kept off the dialog area, sentence resolved by "ASKS FIRST." held 1.875 s - readable enough.
- Directive compliance: PASS. Logo once (shot 16, Magnm, multiply on stone per magnm-brand.md). "CERO" only as Anton type. No voice. Transitions varied (hard cut, colour flip, mask-down, stillness, type-mask, mask-to-box, page fold x2, inversion burst, hard stop, in-field). Pace contrast slow -> build -> burst -> drop -> silence -> calm -> end. References used for technique only.
- Photosensitivity: PASS. Full-frame stone/ink inversion every 0.625 s = 1.6 flashes/s, under the 3/s limit. Do not add extra full-frame flashes on the off-8ths.
- Buildability: FAIL (1 shot). Shot 5's "O counter morphs into the footage box" is not a one-pass HTML/GSAP build (Fix 9). Everything else is buildable.
- Platform rules: PASS. X post = 203 chars raw, 206 with the URL counted as 23. IG 5 hashtags. CTA = magnm-org.vercel.app only.
- Spelling: PASS. Cero, Magnm, magnm-org.vercel.app correct; "gitbarins"/"gitbrains" match the real UI.
- Legal/safety: PASS. Original synthesized music + CC0 Kenney SFX; no copied reference material; no posting.

## Fixes required (numbered, exact)

1. Replace "YOUR HAND ON ENTER." (misleading). Verified in footage: Enter (↵) is shown on Run only in F-HERO and F-SLEEP. F-FLOW, S-FLOW5 and S-PORT - three of the five burst dialogs - show Run with no ↵, and the research (F6, README 80-81, FLOW_FILES.md 16) says Enter does NOT approve a flow from a file. The line also reads as "you press Enter and it runs", the opposite of the point.
   - Shot 14, on-screen text: replace `[S] `AI in your terminal.` / [A] `YOUR HAND` / [A] `ON ENTER.`` with `[S] `AI in your terminal.` / [A] `YOUR CALL.``. In the motion cell replace "Anton lines rise 30 px" with "Anton line rises 30 px".
   - Section D row 14: replace the row with `| 14 | `YOUR CALL.` one line | two lines: `YOUR` / `CALL.` |`
   - Section F row: replace with `| "AI in your terminal. / YOUR CALL." | synthesis: changes wait for your Run or Cancel | footage F-HERO, F-DECL; product-facts approval line. Metaphor, not a feature claim |`
   - Logline (line 12): replace with `Logline: AI in your terminal, and the call stays yours - questions just get answered, and anything that changes your files, system or network shows you the exact command and waits.`
   - Caption first line (line 187): replace with `AI in your terminal, and the call stays yours.`
   (If the CMO prefers, `YOU DECIDE.` is an equally safe alternative.)

2. Replace "It doesn't guess." (absolute the product contradicts). In demo 36-43 Cero answers "claude is running. (You wrote "clude"; that is the closest running process.)" - it picked the closest match without asking. The claim is only true for the folder case shown.
   - Shot 8, on-screen text: replace `[S] `It` [A] `ASKS.` / [S] `It doesn't guess.`` with `[S] `Wrong name?` / [S] `It` [A] `ASKS.``
   - Shot 8, motion: replace "19.688: Anton "ASKS." cuts in. 20.000: lime 3 px outline draws around the UI text `Did you mean "gitbrains"` (0.4 s). 20.313: serif second line mask-rise. Hold to 23.125 (2.8 s)." with "19.375: serif "Wrong name?" mask-rise (0.35 s expo-out) with the box. 19.688: "It" + Anton "ASKS." cut in. 20.000: lime 3 px outline draws around the UI text `Did you mean "gitbrains"` (0.4 s). Hold to 23.125 (Anton readable 3.4 s)."
   - Section D row 8: replace "type top third: `It ASKS.` / `It doesn't guess.`" with "type top third: `Wrong name?` / `It ASKS.`"
   - Section F row: replace with `| "Wrong name? / It ASKS." | asks when a name does not match | product-facts: "asks when two match"; research F7 (README 39-40, 138-159); footage F-ASK "Did you mean "gitbrains"" |`
   - Section B row "Asks, not guesses": rename the film section to "Wrong name, it asks (19.4-23.1)".

3. Replace "NO / means no." (tone). It is the consent-campaign slogan; on a dev-tool ad it risks reading as flippant, with nothing gained. The caption already says it better ("Cancel, and it doesn't run.").
   - Shot 11, on-screen text: replace "28.750: [A] `NO` / 30.000 adds [S] `means no.` beside it." with "28.750: [A] `CANCEL` / 30.000 adds [S] `and it doesn't run.` beside it (16:9) / below it (9:16)."
   - Shot 11, motion: replace `"NO" cuts in on 28.750.` with `"CANCEL" cuts in on 28.750.`
   - Section D row 11: replace `"NO" huge left` with `"CANCEL" huge left` and `"NO" top third` with `"CANCEL" top third`.
   - Section F row: replace `"NO / means no."` with `"CANCEL / and it doesn't run."`
   - Section B row "No / Stop": rename to "Cancel / Stop (28.75-36.9)".

4. Shot 7 lime highlight makes the answer unreadable. The app's answer text is light grey; a #C8F31D fill behind it is about 1.2:1 contrast, so "42.5 (3 rows)" disappears at the moment it should land.
   - Shot 7, motion: replace "16.875: lime highlight block wipes in behind "42.5 (3 rows)" (0.25 s)." with "16.875: lime 4 px underline draws left-to-right under "42.5 (3 rows)" (0.25 s expo-out), 4 px below the text baseline."
   - Section A, colours: replace "(accent, used only for: the answer "42.5", the underline on "Nothing runs until you approve.", the outline on "Did you mean", and the calm field)" with "(accent, used only for: the underline under "42.5 (3 rows)", the underline on "Nothing runs until you approve.", the outline on "Did you mean", the underline on "Declined in the confirmation dialog.", and the calm field)". (The shot 11 underline was missing from the list.)

5. F-Q1 / F-Q2 timing is off and one stray frame must go. Verified frame by frame:
   - frames 293-294 (src 9.767-9.800): empty input, cursor only.
   - frame 295 (src 9.833): grey autosuggest ghost text "make a folder called demo-two and save this as a..." (an unrelated later request).
   - frame 296 (src 9.867): ">what " + grey ghost completion.
   - frame 297 (src 9.900): full request line.
   - about src 10.20 to frame 310: interim grey line "› Total of the score column in data.csv".
   - frame 311 (src 10.367): "✓ The total of the score column in data.csv is 42.5 (3 rows)."
   Replace text as follows:
   - C.0 F-Q1 "Source time" cell: replace "request appears 9.83" with "request line complete at src 9.900 (frame 297). Replace frames 295-296 with frame 294 (they show unrelated autosuggest ghost text)."
   - C.0 F-Q2 "Source time" cell: replace "answer appears ~10.2 (+/-0.2, between 10.0 and 10.5; Producer scrub)" with "interim line "› Total of the score column in data.csv" from about src 10.20; final answer from src 10.367 (frame 311)".
   - Shot 7 visual: replace "so the request lands at 16.250 and the answer at ~16.62" with "so the request line is complete at 16.320 and the answer lands at 16.787 (the grey interim line shows 16.62-16.787; it is real UI, keep it)".
   - Shot 7 visual: replace "(empty plate until 16.250 per footage)" with "(empty input until 16.320 per footage)".
   - Shot 7 sound and section E sync table: replace "16.250: keypress (Enter) synced to the request line appearing" / "| 16.250 | request line appears in footage (src 9.83) | keypress (Enter) |" with "16.320: keypress (Enter)" / "| 16.320 | request line complete in footage (src 9.900, frame 297) | keypress (Enter) |"; replace "~16.62: soft "tick-chime" when the answer line appears (Producer syncs to the actual frame)" / "| ~16.62 | answer line appears (src ~10.2, scrub) | soft tick-chime |" with "16.787: soft tick-chime" / "| 16.787 | answer line appears (src 10.367, frame 311) | soft tick-chime |".
   - Open item 1: replace with "1. Resolved by Reviewer: answer frame = src 10.367 (frame 311)."

6. F-STOP is 2 frames late in the script, and the silence cut depends on it. "Stopped." first appears at frame 3438 = src 114.600 (frames 3436-3437 still show the running state; verified).
   - C.0 F-STOP "Source time" cell: replace `114.53-116.40 ("Stopped." appears at 114.53, frame 3436, verified between 114.50 and 114.57)` with `114.600-116.475 ("Stopped." first visible at frame 3438; frames 3436-3437 still show the running state)`.
   - C.0 F-EXEC "Source time" cell: replace "112.03-114.53" with "112.10-114.60".
   - Shot 12 visual: replace "(source 112.03 onward)" with "(source 112.10 onward)" and "(source 114.53)" with "(source 114.600, frame 3438)".
   - Section E sync row: replace `"Stopped." appears (src 114.53)` with `"Stopped." appears (src 114.600, frame 3438)`.

7. Shot 6 cannot be "real time" as written. Shot 6 lasts 3.75 s but the source given is only 1.75 s (54.60-56.35). Playing 3.75 s real time would run into Cancel (56.467) and the declined terminal.
   - Shot 6 visual: replace "F-HERO continues (source 54.60-56.35, real time; ends before Cancel)." with "F-HERO continues: source 54.60-56.35 real time (film 11.875-13.625), then hold frame 1692 (src 56.40, dialog unchanged, before Cancel) as a still to 15.625."

8. Shot 8 9:16 push rectangle crops the question. Verified: y 280 cuts off the top of line 1 and pulls in "CHOOSE ONE".
   - Section D row 8: replace "(source rect 432,280,1056,140 -> 1016 px)" with "(source rect 432,256,1056,120 -> 1016 px)". Verified: this shows both lines of "I could not find "gitbarins". Did you mean "gitbrains" in ~/cero-demo/Projects?" and nothing else.

9. Shot 5 O-counter morph is not a one-pass build (turning a text counter shape into a bracketed rectangle cannot be interpolated). Simpler version with the same idea:
   - Shot 5 motion: replace "10.625: the O counter scales up (quint-in-out, 0.8 s) until it becomes the footage box of shot 6; other letters slide off left/right." with "10.625: zoom-through. Only the CERO mask scales: about 12x, centred on the O counter (quint-in-out, 1.0 s, 10.625-11.625). The footage layer stays fixed, so the dialog seen through the O fills the frame, and C, E and R leave the frame with the scale. 11.625-11.875: the full-frame footage clips down to the shot 6 box (clip-path inset, expo-out 0.25 s). The corner brackets cut in on 11.875."
   - Transition cell: replace "Mask-to-box (continuous) at 11.875" with "Zoom-through the O, then clip-to-box (continuous) at 11.875".

## Optional notes (non-blocking)
1. Burst b6 `[S] asks…` followed by "ASKS FIRST." reads as a stutter. Consider holding `OR NETWORK` across b5 and b6 (1.25 s, better for readability). F-SLEEP stays behind it on b6.
2. Hook vs strategy: the strategy says the first frame on X/LinkedIn should already be a readable line. Here the first readable line comes at 2.8 s. The directive allows a still opening, so keep it, but set the X/LinkedIn poster/thumbnail frame to shot 10 ("ASKS FIRST.") or shot 6.
3. "See the command BEFORE IT RUNS." is unscoped on its own. In context, shot 7 and the burst give it the right scope. Do not use it alone as a thumbnail or caption without the "Anything that changes your files, system or network" line nearby.
4. Production: for frame-exact sync, render the footage from pre-cropped PNG sequences (ffmpeg -ss/-frames with the C.0 crops) instead of a seeked <video> element in headless Chrome. Put the footage inside the letters with an SVG clipPath built from the Anton text.
5. During the burst, switch the footage label per beat ("real app footage" for F-HERO/F-FLOW/F-SLEEP, "real app screenshot" for S-FLOW5/S-PORT), so the 2.1.0 stills are never labelled as video.
6. Shot 3's readable time in the table says 2.19 s. It is 1.86 s (exit at 7.800). Correct the number; the timing is fine.
7. F-FLOW shows the mouse pointer over Cancel, and its Why text reads "1 steps". Both are real UI and fine for 0.625 s; S-FLOW5 avoids both if the Producer wants a cleaner frame.
