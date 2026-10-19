# Taste rules for video frames and React components
Source: vendor-extra/taste-skill-skills (Leonxlnx/taste-skill, MIT). It is written for websites; here we use its principles for every frame and every React/HTML component the Video Producer builds. Read its taste-skill/SKILL.md and references/{type,colour,tells,motion,layout}.md before building, plus minimalist-skill for our black/white direction.
Apply:
1. Decide before styling: one idea per card, taken from the subject (a command, a number, a file extension), not a category default.
2. Type scale: few sizes, far apart. One huge display word, one italic line, one small mono line per card. Never nine similar sizes.
3. Banned tells (taste tells.md): gradient text, glow, glass blur, drop shadows, pill badges, eyebrow labels over every heading, three identical icon cards, emoji icons, decorative status dots, fade-and-rise on everything with identical timing, bounce/elastic easing, cream+terracotta.
4. Colour: one accent, used for emphasis (the answer, the number, the "in development"), flat fields only.
5. Motion: one signature move (masked word rise), choreographed per card, hard cuts, no layout-property animation. Strobing and shake banned.
6. Imagery: real footage or real interface only; label it. No fake dashboards from grey bars.
7. Copy: plain, specific, claims only from research/product-facts.md. No "seamless", "elevate", "next-gen".
8. Look at the result: snapshot every card at both ratios, fix, look again, before rendering.
Node/Chrome scripts in the taste skill (draw.mjs, inspect.mjs) are optional; they need Chrome and node 22.
