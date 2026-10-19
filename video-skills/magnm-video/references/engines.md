# Engines: which one to use

| Need | Engine | Skill to read |
|---|---|---|
| Fast launch/teaser from a repo or site, AI voice, captions, vertical | HyperFrames (brag-style) | upstream-brag/step-1..4 + vendor/hyperframes-skills/{hyperframes-core,hyperframes-animation,hyperframes-creative,hyperframes-cli,hyperframes-audio} |
| Promo from a product URL or pasted script | HyperFrames | vendor/hyperframes-skills/product-launch-video |
| Concept explainer with no footage | HyperFrames | vendor/hyperframes-skills/faceless-explainer |
| Add captions to a video we already recorded | HyperFrames | vendor/hyperframes-skills/embedded-captions |
| Cut/recut a talking-head clip | HyperFrames | vendor/hyperframes-skills/talking-head-recut |
| Reuse real React components (LazyLayout) in the video | Remotion | vendor/remotion-skills/remotion-best-practices, remotion-create, remotion-markup, remotion-captions, remotion-render |
| Narrated product video built in React code | Remotion | vendor/demo-video/skills/produce |
| Real screen recording of Cero in a terminal, then narration and zooms | macOS recording pipeline | vendor/demo-video/skills/record |
| Older Remotion + TTS (Edge TTS, free) pipeline | Remotion | vendor/remotion-video-skill |
| Convert a Remotion project into HyperFrames | HyperFrames | vendor/hyperframes-skills/remotion-to-hyperframes |

## Why HyperFrames is the default
- Agent-native: compositions are plain HTML, deterministic frames, CLI is non-interactive.
- Apache-2.0 with no per-render fees; brag is MIT.
- Built-in narration (Kokoro), captions, audio mixing, and asset sourcing (`media-use`).

## When Remotion is worth it
- LazyLayout's animations are React: importing them directly makes the demo truthful and cheap to update.
- Check Remotion's current licence terms for a company before commercial use; do not assume it is free for a company.

## Rules for any engine
- Do the engine's own `check`/lint before rendering.
- Run a 15-second test render before a full one.
- Record engine, versions, and asset licences in the output README.md.
