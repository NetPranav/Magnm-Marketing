# BRIEF 20261024-film -> Researcher
From: CMO  |  Date: 2026-10-17  |  Due: today
Why: the CEO wants Magnm's first real motion-graphics film (no voice-over, both 9:16 and 16:9), built per playbooks/video-directive.md and research/reference-style.md. Last test was a feature list and showed LazyLayout, which is not built. This time the topic must be the single strongest story we can prove. You give the Strategist the evidence to choose it.

Inputs:
- Cero repo, cloned read-only in the cloud workspace at /home/claude/repos/Cero-Terminal (README.md, docs/, assets/, docs/media/cero-demo.mp4 = 2.5 min real unedited app demo 1920x1080, docs/media/flow-demo.gif, docs/images/*.png).
- research/product-facts.md (current verified list; extend it, do not loosen it).
- Web: what developers actually complain about with AI in the terminal / AI coding agents running commands (trust, "it ran rm without asking", hallucinated commands, cloud privacy, setup scripts that break across OS, "works on my machine" onboarding). Competitors/alternatives developers name (e.g. Warp, GitHub Copilot CLI, Claude Code, Gemini CLI, shell-gpt, Ollama-based tools). Last 12 months preferred, each with URL + date.

Tasks:
1. Feature inventory of Cero with evidence (file + line or section) - especially: plain-language requests with `>`, approval before anything that changes files/system/network, deterministic built-in model + code routes, local model / Ollama / own API, keychain, .flow files cross-OS with quoted values + audit log, asks when names are ambiguous (never creates when asked to open), Ctrl+C + visible queue, /learn /forget only when told, release/platform/licence facts.
2. Real footage map: extract frames from docs/media/cero-demo.mp4 (ffmpeg) and list usable segments with start-end seconds, what is on screen, legibility at phone size, and whether it is the app or the demo's own title slides (do not reuse their title slides as our design). Also docs/images screenshots and flow-demo.gif.
3. 3-5 candidate film topics, each = one tension a developer feels + Cero's proven answer + the footage that proves it + why it beats a feature list. Rank them.
4. Anything we must NOT claim (unsupported numbers, ROS, LazyLayout anything).

Output: write outputs/research/20261024-film.md on the user's computer via the device shell (folder $HOME/mnt/Magnm-Marketing). Sources section with URLs. Mark guesses as guesses.
Then append one line to state/activity.log: "<ISO time> researcher -> cmo: outputs/research/20261024-film.md done".
Rules: read agents/researcher.md first. Never post anything anywhere. LazyLayout is not built: do not research or propose it.
