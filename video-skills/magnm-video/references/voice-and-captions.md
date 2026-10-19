# AI voice and captions

## Voice (default on)
- Use HyperFrames narration (Kokoro) through the `media-use` / `hyperframes-audio` skills. Upstream brag also uses Kokoro for `--voice`.
- Alternatives when HyperFrames TTS is not available: Edge TTS (free) as in vendor/remotion-video-skill; OpenAI TTS or macOS voices as in vendor/demo-video/skills/produce. Ask the user before using any paid or key-based service, and never put API keys in files.
- Write the narration script first (voiceover.txt), 2.5 words per second as a guide; it must fit the duration. Do not read on-screen text word for word.
- Keep the narration claims identical to the verified claims in plan.md.

## Pronunciation (confirm once with the user, then record here)
- Cero: ____
- LazyLayout: ____
- Magnm: ____

## Captions
- Burned in, 2 lines max, large, safe-zone aware (platforms.md), high contrast on the navy palette.
- Word-level highlight timing from the transcript (HyperFrames `embedded-captions` or Remotion `remotion-captions`).
- Also export captions.srt.
- Proofread names: Cero, LazyLayout, Magnm.

## Audio mix
Voice first. SFX subtle (assets/sfx, CC0). Music optional and only if licensed (see SKILL.md law 7); duck under voice.
