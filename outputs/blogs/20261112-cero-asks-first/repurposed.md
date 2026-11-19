# Repurposed Multi-Channel Campaign Kit
Article: "Why We Built a Terminal AI That Asks Before Executing"
ID: `20261112-cero-asks-first`

---

## 1. X (Twitter) Technical Thread

### Tweet 1 (Hook)
Piping LLM output straight into `bash` is a disaster waiting to happen.

When an AI hallucinates a flag or path, an innocent command can wipe files or leak keys.

We built Cero around one rule: AI in your terminal, but the call stays yours.

Here's how it works 🧵👇

### Tweet 2 (The Asks-First Architecture)
In Cero, start a line with `>` to prompt in plain language.

If the answer is read-only (inspecting git status, checking process trees), it answers from real output.

But if anything modifies your files, system, or network? It stops and shows the exact command first.

### Tweet 3 (Safety Dialog)
You see the raw shell command in an approval modal before a single byte changes.

Cancel declines it. Nothing runs.
Pressing `Ctrl+C` at any point halts execution immediately.

No black boxes. Real shell (`zsh`/`bash`/PowerShell).

### Tweet 4 (Local Models & Privacy)
Your terminal history shouldn't live on someone else's server.

Cero ships with a built-in 3B local model (2.1 GB) that runs completely on-device.
Prefer Ollama or custom API endpoints? You can plug them in, and keys live in the system keychain.

### Tweet 5 (Open Source CTA)
Cero is 100% open source (MIT), built with Tauri v2, Rust, and React 19.

Give it a spin on macOS, Linux, or Windows:
👉 https://github.com/NetPranav/Cero-Terminal

Built by @Magnm_Studios.

---

## 2. LinkedIn Engineering Post (Company Page)
**Title:** Why we designed human approval into AI terminal commands

Most terminal AI tools try to automate everything end-to-end. But the terminal is the most privileged environment on any developer's machine.

When building Cero, our team took a different engineering stance:

1. **Read-only questions just get answered** — using real system outputs.
2. **State-altering commands ask first** — displaying the exact shell syntax for human approval before execution.
3. **Local-first inference** — running a 3B model directly on your hardware, with API keys anchored in the system keychain.

Developer leverage doesn't require handing over the keyboard blindly.

Cero is open source (MIT) and cross-platform: https://github.com/NetPranav/Cero-Terminal

#DeveloperTools #Rust #OpenSource #Commandline #SoftwareEngineering

---

## 3. Short-Form Video Script (30s Reel / YouTube Short)
- **0:00 - 0:03 (Hook):** [Fast zoom on terminal cursor] "Never let an AI run shell commands without showing you first."
- **0:03 - 0:10 (Problem):** [Red flash on simulated scary command] "One hallucinated flag can wipe an entire folder."
- **0:10 - 0:20 (Cero Solution):** [Screen recording of Cero dialog popping up with exact command] "In Cero, plain-language questions answer directly. But anything that touches your files stops and shows the exact command."
- **0:20 - 0:26 (Action):** [Cursor clicks 'Cancel'] "Cancel, and nothing runs. Ctrl+C kills it instantly."
- **0:26 - 0:30 (CTA):** [Cero Logo + GitHub URL] "Cero. AI in your terminal. You make the call. Open source on GitHub."

---

## 4. Card Spec
```json
{
  "id": "cero-asks-first-card",
  "field": "mint",
  "kicker": "CERO TERMINAL",
  "big": ["AI IN YOUR", "TERMINAL."],
  "serif": "and the call stays yours.",
  "foot": "github.com/NetPranav/Cero-Terminal",
  "sizes": ["1x1", "16x9", "9x16", "4x5"]
}
```
