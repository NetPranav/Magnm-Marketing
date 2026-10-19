---
name: magnm-video
description: Generate short social videos (Reels, Shorts, LinkedIn, X, YouTube) for Magnm's products Cero and LazyLayout with AI voiceover, captions and motion graphics. A Magnm fork of /brag that routes between HyperFrames (HTML to video) and Remotion (React). Use when the CMO or user asks for a demo video, launch teaser, build-in-public clip, or explainer.
---

# magnm-video

Magnm's video generator. Forked from the MIT-licensed `/brag` skill (latent-spaces/brag) and extended for dev-tool marketing: vertical-first, AI voice on by default, burned-in captions, per-platform share copy, and a hard honesty rule about what the product actually does.

Folder root: `Magnm-Marketing/video-skills/`. Everything referenced below lives under `magnm-video/` unless it says `vendor/`.

## 0. Inputs (from the CMO brief or the user)

| Input | Values | Default |
|---|---|---|
| product | `cero`, `lazylayout`, `magnm` (company) | required |
| video type | see references/video-types.md | inferred from the brief |
| platform | `instagram-reel`, `youtube-short`, `youtube`, `linkedin`, `x` | `instagram-reel` |
| tone | see references/devtool-tones.md | `builder` |
| voice | on / off | **on** |
| duration | seconds | per platform table in references/platforms.md |
| source | repo path, URL, screen recording, or pasted brief | required |

If the brief is missing product, type, or source, stop and ask the CMO (write the question to the brief file); do not guess a product claim.

## 1. Pick the engine

Read references/engines.md. Short version:
- **HyperFrames** (default). Agent-native HTML to MP4, Apache-2.0, no per-render fees, Kokoro narration. Use the brag-style workflow (references/upstream-brag/step-1..4) or `vendor/hyperframes-skills/product-launch-video` for URL-based promos, `faceless-explainer` for concept explainers.
- **Remotion** (React) when the video must reuse real React components (for example LazyLayout's actual components or animations). Skills in `vendor/remotion-skills/` and the narrated pipeline in `vendor/demo-video/skills/produce`. Check Remotion's licence terms for a company before commercial use.
- **Screen-recording demos** (Cero in a real terminal): `vendor/demo-video/skills/record` turns a macOS recording into a narrated demo.

## 2. Workflow (mirrors brag's four gated steps)

1. **Inspect** (references/upstream-brag/step-1-inspect.md): read the product code, README, and any recordings. Answer the 9 planning questions using only things the product really does.
2. **Plan and storyboard** (step-2-plan.md + devtool-tones.md + platforms.md): write `outputs/video/<id>/plan.md`: hook, beats, on-screen text, narration script, caption style, SFX cues, platform cut-downs. Reels/Shorts: hook in the first 2 seconds.
3. **Compose** (step-3-compose.md, audio.md and voice-and-captions.md): build the composition in `outputs/video/<id>/composition/`. Narration via Kokoro through HyperFrames `media-use`; captions burned in because most viewers watch muted.
4. **Deliver** (step-4-deliver.md): validate, render, pick a poster frame, write per-platform share copy.

Gates are the same as brag's: composition check passes before render; the mp4 exists; the share copy exists.

## 3. Magnm laws (override upstream where they differ)

1. **Real product only.** Every UI, terminal output, number, and claim must come from the repo, a real recording, or the brief. No invented benchmarks, users, testimonials, or logos. If a terminal or UI scene is simulated, label it "illustrative" on screen and in the share copy.
2. **Cero scenes** show a real command and its real output (recorded, or reproduced by actually running it). **LazyLayout scenes** show the real wireframe-to-animation flow.
3. **Vertical first.** Design 9:16 safe zones first, then derive 1:1 and 16:9 cut-downs (references/platforms.md).
4. **Voice on, captions on.** Voice is conversational and complements the visuals. Captions are always burned in. Provide `captions.srt` too.
5. **Hook, show, one claim, one call to action.** 20-45 seconds for Reels/Shorts. Hold every line long enough to read.
6. **Brand consistency** (references/magnm-brand.md): colors, fonts, logo placement, spelling of Cero / LazyLayout / Magnm.
7. **Audio licensing.** Do not use the music bundled in the upstream brag repo (its licence is unverified). Use: no music, music the user supplies with a clear licence, or a licensed/CC0 track recorded in `video-brief.md`. SFX in `assets/sfx` are CC0 (Kenney).
8. **No deceptive content.** No fake reviews, no impersonation, no clones of other brands' characters or ads.

## 4. Outputs (all in `Magnm-Marketing/outputs/video/<id>/`)

- `plan.md` (storyboard and narration script), `composition/` (source), `final.mp4`, `poster.jpg`, `captions.srt`, `voiceover.txt`, `share-copy.md` (per-platform title, caption, hashtags, first comment), `README.md` (how it was made, assets and licences used).

## 5. Handoffs in the Magnm team

The CMO writes a brief from `templates/video-brief.md`. The Video Producer agent (`agents/video-producer.md`) runs this skill, writes outputs, and returns the path. The Reviewer checks claims, spelling, and licences. The user publishes.

## 6. Tooling notes

- Requires Node.js 22+, FFmpeg, and the HyperFrames CLI (`npx hyperframes doctor` to check). Ask the user before running `npx hyperframes skills update` or installing packages.
- Rendering runs in the cloud workspace or on the user's machine, wherever the toolchain is installed. This skill has not been test-rendered yet; the first run is a test and should produce a 15-second clip before a full video.

## Style update 2026-10-17
Before building any video read references/magnm-brand.md (v2), references/taste-for-video.md, and research/reference-style.md in the marketing folder. Always deliver two cuts of the same script: mobile 9:16 (1080x1920) and desktop 16:9 (1920x1080), one voice track, same timing.
Also read playbooks/video-directive.md (CEO master directive: autonomous, reference-driven, finished render, logo rare, voice optional, varied motion, QA the export) and research/reference-style.md (3 analysed references + map + house learnings). These override older rules above (voice-on default, navy brand, logo corner placement).

## LazyLayout rule
HARD RULE (CEO, 2026-10-17): LazyLayout is NOT built. Never show, demo, describe features of, or put LazyLayout visuals in any post or video. At most a text-only "something else is coming" line, and only if the CEO approves it per post. All content is about Cero (finished) or Magnm the team.
