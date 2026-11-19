# `@magnm/design`

Shared monochrome design kit for **Magnm Studio** (React) and **Magnm-Pitch** (vanilla JS).
No build step: plain CSS files and TypeScript React components imported directly from source.

## Rules

- **No accent colour.** Black, white, and warm greys only. Status states use dot/square shape and label text rather than colour.
- **No emojis** in code, UI copy, or prompts.
- **Offline-first fonts.** Instrument Serif, Hanken Grotesk, and DM Mono are bundled as `.woff2` files under `./fonts/` (`OFL.txt`, `FONTS.md`).

## Usage

### In React / Vite (Magnm Studio)

Import the stylesheet once at the app entry point (and call `applyStoredTheme()` before mounting to prevent a theme flash):

```ts
import '@magnm/design/magnm.css';
import { applyStoredTheme, NumberedTabs, PillButton, ThemeToggle } from '@magnm/design/react';

applyStoredTheme();
```

### In Vanilla HTML / JS (Magnm-Pitch)

Link `./magnm.css` (or import `@magnm/design/magnm.css` / `tokens.css` + `fonts.css`) and apply the `.mg-root` class on `<body>`.

## Light / Dark Theme

- Light is the default (`:root` and `[data-theme="light"]`).
- Dark activates via `data-theme="dark"` on `<html>` (or any scoped container). It does not auto-switch via `prefers-color-scheme` CSS media queries; `useTheme()` / `ThemeToggle` resolves `"system"` in JS and persists the choice under `localStorage` key `magnm-theme`.

## Design Tokens (`tokens.css`)

| Token | Light | Dark | Purpose |
|---|---|---|---|
| `--bg` | `#ffffff` | `#0a0a0a` | Page background |
| `--fg` | `#0a0a0a` | `#f5f5f3` | Primary ink / foreground |
| `--body` | `#3b3b3b` | `#c4c4bf` | Body copy |
| `--muted` | `#61615d` | `#969690` | Secondary labels / captions (WCAG AA on `--bg` and `--tint`) |
| `--tint` | `#f5f5f3` | `#161615` | Card surface / hover fill |
| `--tint-2` | `#ebebe8` | `#222220` | Selected fill / notice fill |
| `--line` | `#e3e3df` | `#272724` | Hairline divider |
| `--line-2` | `#cfcfca` | `#3a3a36` | Stronger border / input border |
| `--bar-bg` | `rgba(255, 255, 255, .92)` | `rgba(10, 10, 10, .92)` | Sticky tab / bottom bar backdrop |
| `--shadow-pop` | `0 4px 18px rgba(0, 0, 0, .18)` | `0 4px 18px rgba(0, 0, 0, .55)` | Toast shadow |
| `--f-display` | `'Instrument Serif', 'Times New Roman', serif` | same | Wordmark, headings, big numbers |
| `--f-body` | `'Hanken Grotesk', system-ui, Arial, sans-serif` | same | UI and body text |
| `--f-mono` | `'DM Mono', ui-monospace, Menlo, Consolas, monospace` | same | Uppercase labels, counters, status |
| `--gutter` | `20px` (`32px` at `>= 680px`) | same | Horizontal page gutter |
| `--col-read` | `780px` | same | Reading column max-width |
| `--col-wide` | `1200px` | same | Workspace column max-width |
| `--radius-card` | `14px` | same | Card corner radius |
| `--radius-sm` | `6px` | same | Chip / small radius |
| `--radius-pill` | `99px` | same | Pill button / toast radius |
| `--s-1` .. `--s-8` | `4, 8, 12, 16, 24, 32, 48, 64 px` | same | Spacing scale |

## Tests

```bash
node --test packages/magnm-design/scripts/
```
