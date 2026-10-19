# Script v1: 20261024-film "See it before it runs."
Writer -> CMO / Video Producer / Reviewer | 2026-10-17 | Brief: briefs/BRIEF-20261024-film_writer.md
Inputs: agents/writer.md, playbooks/video-directive.md (read in full), research/reference-style.md, research/product-facts.md, outputs/research/20261024-film.md, outputs/strategy/20261024-film.md, video-skills/magnm-video/references/{magnm-brand,taste-for-video}.md, config.md.
CMO decisions applied: CTA = magnm-org.vercel.app only (no repo link). No "something else is coming". No LazyLayout. No OS names. No prompt "pranav@Pranavs-MacBook", no status bar (every crop below is chosen to exclude both). End line as approved. "Questions just get answered." as approved. One logo moment: Magnm logo at the very end; "Cero" only as Anton type.
No voice-over. Music + sound design only.

Footage check: every source second and crop below was verified by the Writer by extracting frames with ffmpeg from /home/claude/repos/Cero-Terminal/docs/media/cero-demo.mp4 (1920x1080, 30 fps, 147.5 s) and docs/images/*.png (1280x960) and looking at them. Event times found with ffmpeg scene detection + frame checks (accuracy +/-1 frame unless marked).

---------------------------------------------------------------
## A. Logline, duration, platforms

Logline: AI in your terminal, without losing your hand on Enter - questions just get answered, and anything that changes something shows you the exact command and waits.

Duration: 46.875 s (= 75 beats at 96 BPM; 1 beat = 0.625 s, 1 bar = 2.5 s). All times below are on this beat grid.
Platforms: IG Reels (9:16, 1080x1920), YouTube Short (9:16), LinkedIn (16:9 or 9:16), X (16:9, 1920x1080), YouTube (16:9). One timeline, two native layouts. 30 fps, H.264, AAC, -14 LUFS.
Must work muted (LinkedIn/X autoplay): every idea is carried by on-screen type; sound only adds.

Type system (brand): Anton = the one key noun/phrase per card (HUGE). Instrument Serif Italic = connecting words. JetBrains Mono = labels, commands, the cursor. Colours: stone #EFEFEC, ink #0A0A0A, lime #C8F31D (accent, used only for: the answer "42.5", the underline on "Nothing runs until you approve.", the outline on "Did you mean", and the calm field), muted #6B6B66 for mono labels. Subtle generated paper grain on all flat fields. No gradients, glow, shadows, glass.

Footage label (mono, muted, 22 px @1080 wide, bottom-left of each footage box): `real app footage` (video) or `real app screenshot` (PNGs). No version numbers.

---------------------------------------------------------------
## B. Reference intelligence map (this film)
Passages are from research/reference-style.md (the Writer did not re-watch the references; timestamps are the ones recorded there). Techniques only; no subject matter, artwork, lettering or music copied.

| Film section (time) | Ref passage | Technique | Our adaptation |
|---|---|---|---|
| Hook, stillness (0-2.5) | Ref 2, 0-7s footprint build | quiet build: marks appear one by one on a still ground, no cuts, before the first hit | a single mono `>` and a blinking block cursor on stone; nothing else moves. No logo |
| Tension cards (2.5-8.1) | Ref 1, 0-8s "Something HUGE" | huge condensed noun + small italic connector, 1-3 words land per card, lots of negative space | serif "AI in your terminal is" + Anton "USEFUL."; then the flip to ink: serif "Until it runs something" + Anton "YOU DIDN'T READ." |
| Held breath (8.1-9.4) | Ref 2 (fast/slow contrast) + Ref 1 (calm hard cut) | real stillness and near-silence before the reveal | ink field, only the cursor, music drops out |
| Reveal (9.4-11.9) | Ref 2, compositing "footage inside letter-shaped masks / behind giant type" | type-mask into footage | Anton "CERO" (type, not logo) fills the frame; the real hero dialog plays inside the letters, then the O counter scales open into the boxed dialog |
| Hero proof (11.9-15.6) | Ref 1, ~40-60s boxed photos with italic captions; furniture (corner brackets, mono captions) | boxed real footage inside an editorial page | dialog in a box with corner brackets, mono label, italic caption; a lime underline is drawn under the real UI sentence |
| Answer proof (15.6-19.4) | Ref 3, 8-12s page fold; Ref 3, 12-20s giant cropped type drifting / camera pan | page fold into the next section; slow camera drift across huge cropped text | page folds from the hero box to a new page; in 9:16 the real output line is shown 2.2x and the frame drifts along it, landing on "42.5 (3 rows)" |
| Asks, not guesses (19.4-23.1) | Ref 3, same chapter template repeated with a new colour | template rhythm: same layout, new ground | same "boxed footage + serif/Anton pair" template as the hero shot, ground flips to ink |
| Burst (23.1-26.9) + hold (26.9-28.75) | Ref 2, 16-19.5s "MOVE" type burst; Ref 2 black/white inversion on beat | one phrase restyled on the beat; inversion on each beat | the approved sentence "Anything that changes your files, system or network" lands 1-3 words per beat, Anton restyled (solid / outline / condensed-italic / cut) every 8th note, ground inverts stone<->ink on each beat, real approval dialogs flash behind; lands on a held "ASKS FIRST." |
| No / Stop (28.75-36.9) | Ref 2 (calm after burst) + Ref 1 (quick reveal, hold, hard cut) | sudden drop in density; reveal, hold, cut | one dialog, one click, one real output line; then one keycap and "Stopped." |
| Significance (37.5-43.75) | Ref 2, 26-28s full yellow field with one sentence | full accent field, one sentence, calm | full lime field (page fold in), one sentence, then the approved end line in mono |
| Ending (43.75-46.9) | Ref 3, 50-58s packshot -> wordmark; Ref 1, final logo sweep + one URL line | product, then the maker's mark once, then one URL | callback to the opening cursor (now resolved), Anton "CERO", "by" + Magnm logo (once), URL in mono |

Not used on purpose: Ref 3 index tree (the default map's Magnm -> Cero / LazyLayout diagram is banned), Ref 1 phone-notification CTA, Ref 2 grunge stamps/scratches (our texture is subtle grain only).

---------------------------------------------------------------
## C. Shot-by-shot

### C.0 Footage crops (source pixels, x,y,w,h). Use these exact rectangles; all verified to exclude the "pranav@Pranavs-MacBook" prompt and the status bar.
| ID | Source | Source time (s) | Crop x,y,w,h | What is visible (verified) |
|---|---|---|---|---|
| F-HERO | cero-demo.mp4 | 52.10-56.47 (dialog on screen from 52.03 until Cancel at 56.47) | 516,260,888,558 | "Run this command?" / "Needs your approval · sensitive risk" / "Cero will run exactly what is shown below. Nothing runs until you approve." / COMMAND `open '/Users/pranav/cero-demo/Projects/gitbrains'` / Why / Cancel Esc, Run. Margin shows only the blurred app behind. |
| F-HERO-PUSH (9:16 only) | cero-demo.mp4 | same | 560,420,780,230 | sentence "Cero will run exactly what is shown below. Nothing runs until you approve." + the COMMAND line |
| F-Q1 | cero-demo.mp4 | request appears 9.83 | 470,73,700,26 | `>what is the total of the score column in data.csv?` (starts at ">", prompt cut off) |
| F-Q2 | cero-demo.mp4 | answer appears ~10.2 (+/-0.2, between 10.0 and 10.5; Producer scrub) | 26,108,840,26 | `✓ The total of the score column in data.csv is 42.5 (3 rows).` |
| F-ASK | cero-demo.mp4 | 47.00-50.75 (static dialog) | 432,204,1056,668 | "I could not find "gitbarins". Did you mean "gitbrains" in ~/cero-demo/Projects?" / CHOOSE ONE / 1 Yes, open it / 2 No, search again / 3 Cancel / Press 1 to 3... / Cancel Esc. (Only the question; we never show what happens after "Yes".) |
| F-DECL | cero-demo.mp4 | 56.47-58.97 (line appears 56.47) | 26,242,1180,62 | `› Looking for the folder "gitbarins"` / `✗ Could not open ~/cero-demo/Projects/gitbrains: Declined in the confirmation dialog.` |
| F-FLOW | cero-demo.mp4 | 84.80-85.40 (still-like; cursor over Cancel) | 516,186,888,706 | "Run this command?" / "These commands come from a file. Cero will run exactly what is shown below, only after you click Run." / COMMANDS, IN ORDER `1. cd '/Users/pranav/cero-demo' && mkdir -p "demo-two"` / Why... / Cancel, Run |
| F-SLEEP | cero-demo.mp4 | 107.20-107.80 (dialog appears 107.03, Run at ~109.37) | 516,260,888,558 | "Run this command?" ... COMMAND `sleep 40` / Why "Execute command: sleep 40" / Cancel, Run |
| F-EXEC | cero-demo.mp4 | 112.03-114.53 | 26,108,400,26 | `› Execute command: sleep 40` + block cursor |
| F-STOP | cero-demo.mp4 | 114.53-116.40 ("Stopped." appears at 114.53, frame 3436, verified between 114.50 and 114.57) | 20,176,200,26 | `Stopped.` (muted grey in the app). The prompt line directly above it (y 144-166) is excluded. Compose F-EXEC above F-STOP with a 24 px gap. |
| S-PORT | docs/images/close-port.png | still | 312,240,656,480 | "Run this command?" / "Needs your approval · admin risk" / "...only after you click Run. It never asks for your password." / COMMAND `kill 12121` / Why "Stop Python (PID 12121), listening on port 8765" / Cancel, Run |
| S-FLOW5 | docs/images/flow-approval.png | still | 312,148,656,664 | "Run these commands?" / COMMANDS, IN ORDER 1-5 (mkdir -p demo-project, git init, npm init -y, ls -la, open -a 'textedit') / Cancel, Run |

Never used: demo slides (0-8, 32-36, 60-64, 78-82, 95-99, 118-122, 142-147.5), the flow output with the `__cero_step()` line (88-95), anything after "Yes, open it", any full-width frame (prompt + status bar), the old Sentinal-Terminal URL.
Note for CMO: the command strings in F-HERO, F-FLOW and S-FLOW5 contain the path "/Users/pranav/...". This is not the forbidden prompt string, and the exact command is the point of the film, so v1 keeps it. If the CEO wants it gone, use F-SLEEP and S-PORT (no username) in shots 5-6 and 11 instead; timing stays the same.

### C.1 Shots
Text notation: [A] = Anton, [S] = Instrument Serif Italic, [M] = JetBrains Mono. "/" = line break. Easing: "expo-out" = cubic-bezier(0.16,1,0.3,1); "quint-in-out" = cubic-bezier(0.83,0,0.17,1). No bounce/elastic anywhere.

| # | start-end (s) | phase | visual | on-screen text EXACT | motion | transition to next | sound cue |
|---|---|---|---|---|---|---|---|
| 1 | 0.000-2.500 | A Hook | Stone, grain. Nothing but a mono prompt at optical centre (slightly left). | 0.00: [M] `█` (block cursor, ink) / 1.250: [M] `>` typed, cursor moves right: `>█` | Cursor blinks on the beat (on 0.000, off 0.3125, on 0.625...). `>` appears in one frame at 1.250 (no animation). No other motion. | Hard cut on 2.500 | Room tone (very low noise). Cursor: no sound. 1.250: one soft keypress (Kenney). Sub drone D1 fades in 0->-30 dB. |
| 2 | 2.500-5.000 | B Tension | Stone. `>█` stays, moved small to bottom-left (24 px mono, muted) as an anchor. Type centred-left. | [S] `AI in your terminal is` / [A] `USEFUL.` (Anton huge, ink) | Serif line: mask-rise 0.35 s expo-out at 2.500. Anton: lands as one hard frame at 2.813 (8th note later) - no rise. Hold to 5.000 (Anton readable 2.19 s). | Hard cut to INK on 5.000 (colour flip = first hit) | 2.813: soft low thud (Kenney impact, low-passed) on "USEFUL.". Drone continues. |
| 3 | 5.000-8.125 | B Tension | Ink, grain. Anchor `>█` bottom-left now stone. | [S] `Until it runs something` / [A] `YOU DIDN'T` / [A] `READ.` (Anton stone on ink, two lines, huge) | Serif: types in character by character (mono rhythm on a serif, 0.6 s) from 5.000. Anton: both lines cut in at 5.938 (beat 1.5). Hold to 8.125 (Anton readable 2.19 s). | Anton + serif mask DOWN out (0.3 s quint-in) at 7.800; anchor cursor glides to centre. | Each serif character: very quiet tick (keypress at -30 dB) - stops at 5.6. 5.938: deeper thud. Drone swells slightly then cuts at 8.125. |
| 4 | 8.125-9.375 | Held breath | Ink, empty. Only `█` centred, blinking. | [M] `█` | Stillness. One blink. | Type-mask into footage: on 9.375 "CERO" cuts in as the mask. | 8.125-9.375: silence (room tone only, -50 dB). |
| 5 | 9.375-11.875 | C Reveal | Ink. Anton "CERO" fills 90% of frame width (stone outline 2 px). Inside the letters: F-HERO playing (source 52.10-54.60), scaled so the "Run this command?" title sits in the C/E. | [A] `CERO` (as type, not a logo) | 9.375: letters cut in already filled with footage. Slow push-in 100->106% over 1.25 s. 10.625: the O counter scales up (quint-in-out, 0.8 s) until it becomes the footage box of shot 6; other letters slide off left/right. | Mask-to-box (continuous) at 11.875 | 9.375: first downbeat - soft wide impact + music enters (filtered 8th-note synth pulse, D minor). 10.625: low whoosh-free "air" swell under the O opening (synth noise, short). |
| 6 | 11.875-15.625 | C Reveal / M1 | Stone page. Footage box with thin ink corner brackets; F-HERO continues (source 54.60-56.35, real time; ends before Cancel). Label [M] `real app footage` bottom-left of box. Caption above box. | [S] `See the command` / [A] `BEFORE IT RUNS.` | Caption: serif mask-rise at 11.875 (0.35 s expo-out); Anton slides in from left 40 px with opacity cut (not fade) at 12.188. 13.125: lime underline (4 px) draws left-to-right under the UI sentence "Nothing runs until you approve." in 0.5 s expo-out. Hold all to 15.625 (Anton readable 3.4 s, underline 2.5 s). | Page fold (right edge folds over to the left like a page turn, 0.5 s quint-in-out) 15.313-15.625; the back of the page is stone. | 12.188: soft ui click. 13.125: pencil-like "scratch" is NOT used; use a soft high pluck on the underline. Hi-hat 8ths enter at 11.875. |
| 7 | 15.625-19.375 | D Proof / M2 | Stone. Two real output strips stacked: F-Q1 (top) and F-Q2 (below), on an ink plate (the app's own background colour) with 24 px padding. Source plays 9.205-12.955 so the request lands at 16.250 and the answer at ~16.62. Label [M] `real app footage`. | [S] `Questions` / [A] `JUST GET` / [A] `ANSWERED.` + small [M] `read-only · no dialog` under the strips (muted) | Request strip visible from 15.625 (empty plate until 16.250 per footage). Answer appears per footage. 16.875: lime highlight block wipes in behind "42.5 (3 rows)" (0.25 s). Serif at 16.875, Anton at 17.188 (cut). Hold to 19.375 (Anton readable 2.19 s). | Hard cut on 19.375 | 16.250: keypress (Enter) synced to the request line appearing. ~16.62: soft "tick-chime" when the answer line appears (Producer syncs to the actual frame). Kick enters on every beat from 15.625. |
| 8 | 19.375-23.125 | D Proof / M3 | INK page (same template as 6, new ground). F-ASK in box (source 47.00-50.75, static). Label [M] `real app footage`. | [S] `It` [A] `ASKS.` / [S] `It doesn't guess.` | 19.375: box cuts in. 19.688: Anton "ASKS." cuts in. 20.000: lime 3 px outline draws around the UI text `Did you mean "gitbrains"` (0.4 s). 20.313: serif second line mask-rise. Hold to 23.125 (2.8 s). | Burst starts: inversion on 23.125 | 19.375: ui pop (dialog appear). Pulse filter opening (build). 22.500-23.125: riser made from the pulse (no whoosh SFX). |
| 9 | 23.125-26.875 | D Energy peak / M1 | Ground inverts every beat (stone/ink/stone/ink/stone/ink). Behind the type, one real dialog per beat, box ~60% frame, slight 2% scale drift: b1 23.125 F-HERO (src 54.0 still), b2 23.750 F-FLOW, b3 24.375 S-FLOW5, b4 25.000 S-PORT, b5 25.625 no dialog (type only), b6 26.250 F-SLEEP. | Per beat, [A] in front of the dialog, restyled every 8th note (solid -> outline): b1 `ANYTHING` / b2 `THAT CHANGES` / b3 `YOUR FILES,` / b4 `SYSTEM` / b5 `OR NETWORK` / b6 `[S] asks…` (serif, small, a breath before the hold) | Hard cuts on every beat; type style swap on the off-8th (solid fill -> 2 px outline -> next word). Type always ink-on-stone or stone-on-ink (never on the dialog's busy area: dialog sits lower, type upper third). | Hard cut to hold card on 26.875 | Full beat. Each beat: soft impact (not loud, -18 dBFS peak) + inversion. Each off-8th type swap: tiny ui click. |
| 10 | 26.875-28.750 | D Peak hold / M1 | Stone, empty, grain. | [A] `ASKS FIRST.` (largest type in the film, one line; nothing else on the card - the burst already carried the rest of the sentence) | Cuts in on 26.875, holds still 1.875 s. No motion. | Hard stop - cut on 28.750 | 26.875: biggest hit of the film (wide low impact + reverb tail), then music drops to sub + pulse only. |
| 11 | 28.750-32.500 | D Proof / M4a | Stone. F-HERO box again (source 55.22-56.47, the dialog's last 1.25 s), then at 30.000 the box content cuts to F-DECL (source 56.47-58.97) on its ink plate, same box position. Label [M] `real app footage`. | 28.750: [A] `NO` / 30.000 adds [S] `means no.` beside it. Under the strips the footage line itself carries the proof: "Declined in the confirmation dialog." (lime underline on these words at 30.625) | Low density: one box, one word. "NO" cuts in on 28.750. 30.000: footage cut (source cut exactly at 56.47 = Cancel). 30.625: underline draws 0.4 s. Hold to 32.500 (serif 2.5 s, F-DECL line 2.5 s). | Hard cut to INK on 32.500 | 28.750-30.000: near-silence, sub only. 30.000: single soft ui click (Cancel/Esc - dialog closes in footage on this frame). No impact. |
| 12 | 32.500-36.875 | D Proof / M4b | Ink. F-EXEC strip (source 112.03 onward) centred on its plate; F-STOP strip appears under it per footage at 35.000 (source 114.53). Left of the strips: a mono keycap graphic. Label [M] `real app footage`. | 32.813: [M] keycap `Ctrl+C` (stone outline keycap, mono) / 35.000: [S] `means` [A] `STOP.` (reads "Ctrl+C means STOP."; the footage line "Stopped." is the proof) | Keycap appears 32.813 and sits still. 34.875-35.000: keycap depresses 4 px (2 frames) = the press. 35.000: "Stopped." appears in footage; serif "means" and Anton "STOP." cut in on the same frame. Hold to 36.875 (1.875 s). | Hard cut to empty ink on 36.875 (no fade) | 32.500: ticking clock-like pulse at half volume (the request is "running"). 34.875: keypress (ctrl), 34.938: keypress (C). 35.000: music cuts to SILENCE (the stop is heard). Silence continues through 37.500. |
| 13 | 36.875-37.500 | Breath | Ink, empty. | none | Stillness, 0.625 s. | Page fold: an ink page folds away (0.5 s quint-in-out, 37.000-37.500) revealing lime | Silence. 37.250: very soft paper-fold noise (synthesized, not a whoosh). |
| 14 | 37.500-41.250 | E Significance | Full LIME field, grain. Type ink. | [S] `AI in your terminal.` / [A] `YOUR HAND` / [A] `ON ENTER.` | Serif mask-rise 37.500 (0.4 s expo-out). Anton lines rise 30 px with opacity cut at 38.125, slow drift up 1% across hold. Hold to 41.250 (Anton readable 3.1 s). | Continuous - Anton exits up (mask) at 41.000, mono enters in same field | 37.500: warm pad chord (Dmaj9) enters, no drums. Single soft sine pluck on 38.125. |
| 15 | 41.250-43.750 | E Significance | Lime field continues. | [M] `Open source (MIT).` / [M] `The built-in model runs on your computer.` (ink, 44 px @1920 wide / 40 px @1080 wide) | Line 1 at 41.250, line 2 at 41.875, both by character reveal (0.4 s each). Hold to 43.750 (line 2 readable 1.875 s). | Hard cut to stone on 43.750 | Pad continues, filter closing. Two very soft ticks on line starts. |
| 16 | 43.750-46.875 | F Ending | Stone, grain. Callback: the opening `>█` at the same position as shot 1. | 43.750: [M] `>█` / 44.375: [A] `CERO` (ink, medium-large) / 45.000: [S] `by` + Magnm logo (multiply blend, the only logo in the film) / 45.625: [M] `magnm-org.vercel.app` | 43.750 cursor blinks once. 44.375: cursor slides right and "CERO" types out of it (Anton, 4 letters on 16th notes). 45.000: "by" + logo cut in below. 45.625: URL cut in. Hold everything still to 46.875 (URL readable 1.25 s; whole end card 2.5 s). Last frame = full end card (no fade to black). | End | 44.375: four soft keypresses (one per letter). 45.000: low resolving note (D) + soft impact. Tail decays to -inf by 46.875 (no abrupt cut). |

Readability check: every key line holds >= 1.2 s after it is fully on screen (shortest: shot 16 URL 1.25 s; shot 9 burst words are 0.625 s each by design - the sentence is completed by "ASKS FIRST." held 1.875 s). Max words per card: 7 (shot 15 line 2, the CMO-approved end line split onto its own line). No paragraphs.

Transitions used (varied): hard cut (1-2, 7-8, 15-16), colour-flip hard cut (2-3, 11-12), mask-down exit (3-4), stillness (4, 13), type-mask into footage (4-5), mask-to-box scale (5-6), page fold (6-7, 13-14), inversion burst (8-9-10), hard stop (10-11), continuous in-field change (14-15).

---------------------------------------------------------------
## D. Layout notes: 9:16 (1080x1920) vs 16:9 (1920x1080)
Gutters: 9:16 = 64 px sides, 16:9 = 120 px sides. Safe area 9:16: keep text out of the bottom 320 px and top 220 px (IG UI). Footage is never shown full-frame in either ratio.

| # | 16:9 | 9:16 |
|---|---|---|
| 1, 4, 16 | cursor at x 46%, y 50% | cursor at x 40%, y 46% |
| 2 | serif + Anton on one left-aligned block; "USEFUL." ~260 px cap height | same text; "USEFUL." ~220 px cap height, fills width minus gutters |
| 3 | `YOU DIDN'T` / `READ.` two lines, left-aligned | three lines: `YOU` / `DIDN'T` / `READ.`; serif breaks `Until it runs` / `something` |
| 5 | "CERO" spans 90% width, one row | "CERO" in one row at 92% width (~300 px tall letters); footage inside the letters scaled 2x so the dialog title reads through them |
| 6 | box = F-HERO at 1.1x (977x614), right half of frame; caption left column | box = F-HERO at 1016 px wide (1.14x) in the lower-middle; caption above it; at 13.125 the box pushes in to F-HERO-PUSH (780 px source -> 1016 px, 1.3x) so the sentence + command fill the width, underline drawn there |
| 7 | strips at 1.25x on one plate, left-aligned under the caption | strips at 2.2x (F-Q2 = 1848 px wide) on a plate wider than the frame; camera drifts right-to-left 15.625-18.5 (linear, slow) so the line is read as it passes and the drift settles on "42.5 (3 rows)."; F-Q1 drifts the same way above it. Caption in the top third: `Questions` / `JUST GET` / `ANSWERED.` |
| 8 | box F-ASK at 0.85x (898x568), right; type left | box at 1016 px wide (0.96x) in lower half; at 20.0 push to the question line only (source rect 432,280,1056,140 -> 1016 px) for the outline; type top third: `It ASKS.` / `It doesn't guess.` |
| 9 | dialogs at ~60% frame height, centre-right; words upper-left | dialogs at 1016 px wide, lower half; words top third, one word per line (`THAT` / `CHANGES`, `YOUR` / `FILES,`, `OR` / `NETWORK`) |
| 10 | `ASKS FIRST.` one line | `ASKS` / `FIRST.` two lines |
| 11 | box centred-right, "NO" huge left | "NO" top third; F-HERO box then F-DECL below; F-DECL shown at 2.0x with the same right-to-left drift, settling on "Declined in the confirmation dialog." |
| 12 | keycap left, strips right at 1.6x | keycap top-centre; F-EXEC at 2.4x (960 px) and F-STOP at 2.4x below; Anton line under them |
| 14 | `YOUR HAND ON ENTER.` may sit on one line if it fits at the chosen size; else two lines as written | three lines: `YOUR` / `HAND ON` / `ENTER.` |
| 15 | two mono lines, left-aligned | line 2 breaks: `The built-in model` / `runs on your computer.` |
| 16 | "CERO" + "by [logo]" + URL stacked left-centre | same stack, centred; logo width 360 px |

---------------------------------------------------------------
## E. Sound score (original synthesized music + CC0 Kenney SFX only, no voice, no licensed music)
Tempo 96 BPM (beat 0.625 s), key D minor resolving to D major at the end. Mix target -14 LUFS integrated, true peak <= -1 dBTP. SFX packs: Kenney "Interface Sounds" (ui clicks/pops), Kenney "Impact Sounds" (soft impacts), Kenney keyboard keypress samples (CC0). Synth: sine/triangle sub, filtered saw pulse, noise hats, soft pad - all generated.

Sections:
| Time (s) | Section | Music |
|---|---|---|
| 0.000-8.125 | Quiet open | room tone + D1 sine drone fading in; no rhythm |
| 8.125-9.375 | Silence | room tone only (held breath) |
| 9.375-23.125 | Build | 9.375 filtered 8th-note pulse; 11.875 noise hats (8ths); 15.625 soft kick on every beat; 19.375-23.125 low-pass opens; 22.5 riser made from the pulse |
| 23.125-28.750 | Burst | full groove, impact + inversion on each beat, ui ticks on off-8ths; 26.875 biggest hit + tail |
| 28.750-35.000 | Drop / tension | sub + soft half-time pulse only; no kick |
| 35.000-37.500 | Silence | hard cut to silence on "Stopped." (the stop is heard) |
| 37.500-43.750 | Calm | Dmaj9 pad, one sine pluck, filter slowly closing |
| 43.750-46.875 | End | 4 keypresses, resolving low D + soft impact, natural decay to silence |

Sync points (film time -> event -> sound):
| Film (s) | Picture event | Sound |
|---|---|---|
| 1.250 | `>` typed | keypress (soft) |
| 2.813 | "USEFUL." lands | low soft thud |
| 5.000 | cut to ink | colour flip; drone swell |
| 5.0-5.6 | serif types in | quiet keypress ticks (-30 dB) |
| 5.938 | "YOU DIDN'T READ." lands | deeper thud |
| 8.125 | type exits, cursor alone | all music out |
| 9.375 | "CERO" mask with real dialog | first downbeat impact, pulse starts |
| 10.625 | O opens to the box | noise swell (short) |
| 12.188 | "BEFORE IT RUNS." | ui click |
| 13.125 | underline under "Nothing runs until you approve." | soft high pluck |
| 15.313-15.625 | page fold | paper-fold noise (synth) |
| 16.250 | request line appears in footage (src 9.83) | keypress (Enter) |
| ~16.62 | answer line appears (src ~10.2, scrub) | soft tick-chime |
| 19.375 | "Did you mean" dialog box | ui pop |
| 23.125, 23.750, 24.375, 25.000, 25.625, 26.250 | burst beats, ground inverts, dialog changes | soft impact each (-18 dBFS peak) |
| off-8ths 23.4375 ... 26.5625 | type style swap | tiny ui click |
| 26.875 | "ASKS FIRST." | biggest hit + reverb tail |
| 30.000 | Cancel: dialog closes, "Declined in the confirmation dialog." appears (src 56.47) | single soft ui click |
| 34.875 / 34.938 | keycap press (Ctrl, C) | two keypresses |
| 35.000 | "Stopped." appears (src 114.53) + "means STOP." | music cuts to silence |
| 37.250 | page fold to lime | soft paper-fold noise |
| 37.500 | lime field | pad enters |
| 44.375-44.563 | "CERO" types (16ths) | 4 keypresses |
| 45.000 | "by" + Magnm logo | low D resolve + soft impact |
| 46.875 | end | tail at silence |

Rules: no whoosh packs, no impacts on transitions that do not have a visual hit, no SFX implying actions not on screen (the only clicks are on Cancel/type swaps; keypresses only where a key is typed or a keycap is pressed).

---------------------------------------------------------------
## F. Claims check (every on-screen claim -> source)
| On-screen line | Claim | Source |
|---|---|---|
| "AI in your terminal is / USEFUL." | opinion setup, no product claim | Strategist sec 4 (tension); no fact claimed |
| "Until it runs something / YOU DIDN'T READ." | describes a general fear, names no product | research W1-W3 (third-party incidents; not named on screen) |
| "CERO" | product name | product-facts: Cero |
| "See the command / BEFORE IT RUNS." | changes are shown for approval before running | product-facts: "shows any that change things for approval, runs them"; research F2 (README 194 "show exactly what will run"); visible UI text in F-HERO: "Cero will run exactly what is shown below. Nothing runs until you approve." |
| "Questions / JUST GET / ANSWERED." + "read-only · no dialog" | read-only requests run without a dialog, answered from real output | CMO decision 6 (wording approved, proof demo 8-16 s); product-facts: "answers from real output"; research F2a (README 30); footage F-Q1/F-Q2 show no dialog |
| "It ASKS. / It doesn't guess." | asks when a name does not match | product-facts: "Finds folders/apps by name, asks when two match"; research F7 (README 39-40, 138-159); footage F-ASK "Did you mean "gitbrains"" |
| "ANYTHING / THAT CHANGES / YOUR FILES, / SYSTEM / OR NETWORK" -> "ASKS FIRST." | changes ask first | research F2 (README 194: "Commands that change files, the system or the network ask first"); Strategist sec 3 approved wording "Anything that changes your files, system or network asks first."; research Do-not-claim #8 (correct form) |
| Burst dialogs: kill 12121, flow file list, sleep 40, open | real approval dialogs | real footage / repo screenshots (C.0) |
| "NO / means no." + footage "Declined in the confirmation dialog." | Cancel means the command did not run | footage F-DECL (demo 56.47); research map row 56-59 ("proof Cancel works"; never "folder opened") |
| "Ctrl+C means STOP." + footage "Stopped." | Ctrl+C stops a running request | product-facts: "Ctrl+C stops a request"; research F8; footage F-STOP (demo 114.53) |
| "AI in your terminal. / YOUR HAND / ON ENTER." | synthesis: you approve; Run is bound to Enter in the dialog (↵ shown on the Run button in F-HERO) | footage F-HERO (Run ↵); product-facts approval line. Metaphor, not a feature claim |
| "Open source (MIT)." | licence | product-facts: "v2.2.0, MIT"; CMO decision 5 |
| "The built-in model runs on your computer." | built-in local model | product-facts: "Built-in local model ... runs on your computer"; CMO decision 5 |
| "CERO by [Magnm logo]" | Cero is Magnm's product | config.md Products |
| "magnm-org.vercel.app" | CTA | CMO decision 1; magnm-brand.md Site |
| Labels "real app footage" / "real app screenshot" | footage is the real app | research 2a/2c (real unedited clips; screenshots from the 2.1.0 build, so the label says "screenshot", no version) |

Deliberately NOT on screen: OS names, ROS/robotics, "100% local"/offline, "never runs without asking", "safe/secure/signed", numbers/stats, competitor names, model sizes, GitHub link, LazyLayout or any "coming soon" line. "It never asks for your password." is visible inside S-PORT for 0.625 s as UI text only; it is never set as our type.

---------------------------------------------------------------
## G. Post copy (for the CMO; not posted)
Title (YouTube / LinkedIn video title): See it before it runs. | Cero
Hook (first 3 s, muted): stone frame, `>█`, then "AI in your terminal is USEFUL." at 2.8 s - the first frame is already a readable prompt.

Caption (IG / LinkedIn):
AI in your terminal, with your hand still on Enter.

Cero answers questions from real output. Anything that changes your files, system or network asks first and shows the exact command. Cancel, and it doesn't run. Ctrl+C stops a request.

Open source (MIT). The built-in model runs on your computer.

magnm-org.vercel.app

X (16:9, <= 280 chars):
Questions just get answered. Anything that changes your files, system or network shows you the exact command first.
Cero. Open source (MIT). The built-in model runs on your computer.
magnm-org.vercel.app

Hashtags (IG only, 5): #terminal #developertools #opensource #commandline #buildinpublic
Claims in captions = same sources as section F (no new claims).

## Open items for the Producer / CMO
1. Scrub the answer frame for F-Q2 (between src 10.0 and 10.5; scene changes at 10.17 / 10.37) and land the tick-chime on it.
2. "/Users/pranav/..." appears inside F-HERO, F-FLOW, S-FLOW5 command strings (not the forbidden prompt). Kept in v1; swap plan in C.0 if the CEO objects.
3. S-PORT and S-FLOW5 are from the 2.1.0 build (research 2c); same UI as the 2.2 demo. Labelled "real app screenshot".
4. 35 s cut (if needed): drop shot 8 (It asks) and shorten shot 3 to 4 beats; music re-grid by removing 8 beats.
