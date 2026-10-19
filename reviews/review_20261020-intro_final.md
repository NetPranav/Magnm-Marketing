# Review 20261020-intro final cut
Verdict: APPROVE

Scope: text artifacts only (plan.md, voiceover.txt, captions.srt, share-copy.md, README.md) and the visible text nodes of composition/index.html (scripts and styles stripped with python, HTML comments listed separately). I could not view or hear final_ig.mp4 or poster.jpg; everything under "Must be eyeballed" is unverified. I did not run any code from the folder.

## Checks (pass/fail + note)
- Accuracy: PASS. Voiceover, captions and on-screen text only say: small team of developers; Cero finished, cross-platform, local 3B model plus API connector, AI in the terminal; LazyLayout in development, wiring by hand, AI assistant builds complex animations from wireframes. No new claims, numbers, dates or features.
- Script match: PASS (checked by script). voiceover.txt equals the Narration paragraph of draft_v3.md exactly (45 words). The 14 caption phrases in captions.srt, joined with spaces, equal that narration exactly. captions.srt phrases equal the 14 caption divs in index.html and captions.json (same text, same start times). composition/assets/vo/s1-s3.txt are the same three sentences.
- On-screen text: PASS. Complete visible list in index.html: Magnm / Small team. Two products. / FINISHED / Cero / AI in your terminal / terminal / $ / cursor block / Cross-platform / Local 3B model / API connector / AI in your terminal / IN DEVELOPMENT / LazyLayout / AI / Illustration / Magnm / magnm-org.vercel.app / @magnm.studios and @Magnm_Studios / captions. This matches plan.md and the approved beat list, and adds no claim. "AI in your terminal" is a restatement of the approved claim.
- "Illustration" label: PASS with a note. Present in scene 3 (#illus, bottom-right of the wireframe card). It is 24px in the muted colour on a 1080px-wide frame and only fades in at 12.2s, about 1s after the scene starts at 11.2s. See fix 3.
- No faked product output: PASS. The Cero terminal shows only "$" and a blinking cursor, no commands or output. LazyLayout is an abstract wireframe/wire diagram with an "AI" node, labelled Illustration, no UI chrome, no numbers. No metrics or testimonials anywhere.
- Spelling of names: PASS. Cero, LazyLayout, Magnm, @magnm.studios, @Magnm_Studios, magnm-org.vercel.app are correct in index.html, captions, share-copy and plan. Handles match config.md.
- share-copy.md vs draft_v3.md: PASS (checked with python, not by eye). The files differ only in headings/wrapper (share-copy has its own title, an Attach line, a YouTube note, and drops the script, beat list and claims table). Section bodies compared as strings: Instagram caption identical, LinkedIn identical, X post identical (draft_v3 "Single post:" prefix and character-count line are excluded). X length recomputed: 277 with the URL counted as 23, under 280. The YouTube note ("No channel exists yet") is new text, not from draft_v3, and contradicts config.md only in wording (config says channel set up, link TBD). See fix 4.
- Licences recorded: PARTIAL. README.md and plan.md say: Kenney sounds CC0, Inter and JetBrains Mono OFL, GSAP bundled, logo Canva-generated. No licence or credits file is shipped with the assets, the Kenney pack name and source URL are not recorded, GSAP's licence ("Standard no charge license", per package-lock.json) is not stated in README, and the Canva logo's terms are not noted. See fix 1.
- README honesty: PASS. States caption phrase timing is proportional to character counts (approximate, sentence boundaries exact), no word-level transcription, no product footage, no real Cero or LazyLayout recording, scenes 2 and 3 to be replaced when real footage exists. One inaccuracy: "four tiny UI sounds" but only three distinct files exist (click1.ogg is used twice, four cues). See fix 2.
- Tone/platform/legal: PASS. No hype words, no music, no copied content, nothing is posted; humans publish.

## Fixes required (numbered, exact)
All are documentation or polish; none changes a claim, so the verdict is APPROVE. Fixes 1 and 2 should be done before the package is called final.
1. Record licences properly: add outputs/video/20261020-intro/CREDITS.md (or a "Licences" section in README.md) listing, per asset: Kenney sound pack name plus source URL plus CC0, files click1.ogg / switch10.ogg / rollover2.ogg; Inter and JetBrains Mono with OFL-1.1 and source; GSAP 3.14.2 with its "Standard no charge license" (https://gsap.com/standard-license); Kokoro-82M voice model and its licence; Magnm logo generated in Canva (outputs/visuals/magnm-logo.jpg) with a note that it is Magnm's own logo. Do not invent URLs; copy them from the real downloads.
2. In README.md line 7 and plan.md line 7, change "four tiny UI sounds" / "4 CC0 Kenney UI sounds" to "three UI sound files (four cues)".
3. Optional polish: make the "Illustration" label easier to see (larger than 24px, higher contrast than --muted) and show it from the start of scene 3 (11.2s) instead of 12.2s, so no frame of the wireframe appears without it.
4. In share-copy.md, change the YouTube note to match config.md: "YouTube channel is set up but the link is TBD; upload as a Short once the CMO has the link." Do not change the Instagram, LinkedIn or X text.
5. Optional: add the end-card "Magnm" wordmark to the on-screen text list in plan.md so the list is complete.

## Must be eyeballed in the video (I could not view it)
- final_ig.mp4 is the render of the current composition/index.html (same text, same timings); the folder timestamps match but I did not verify the render.
- The "Illustration" label is actually visible and readable on a phone screen throughout the LazyLayout diagram.
- Terminal frame shows only "$" and the cursor; nothing resembling real output appears at any point (including mid-animation).
- Burned-in captions: spelling, no clipping at the 1080px edges, not covering the pills or the diagram, and approximate phrase timing is acceptable against the voice (README admits it is approximate).
- Voice pronounces "Cero", "LazyLayout", "Magnm", "3B" and "API" acceptably; no clipping or pops at scene cuts; UI sounds are quiet (volume 0.3).
- Scene text does not overflow: "IN DEVELOPMENT" chip, "Local 3B model" and "API connector" pills, end card handles line "@magnm.studios · @Magnm_Studios".
- Logo reads correctly (Magnm spelling) at the start and on the end card; end card shows the right URL.
- poster.jpg (suggested cover frame) shows no wrong text and is not a blank or mid-transition frame.
- Total length is 22s and the video ends cleanly after the end card.
