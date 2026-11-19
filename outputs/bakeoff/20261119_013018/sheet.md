# Model Bake-Off Rating Sheet

- Role: writer
- Timestamp: 20261119_013018
- Briefs: 3

Rate each option from 1 (poor) to 5 (ready to publish).
Save ratings as JSON: `{"<brief_id>": {"A": 4, "B": 3, ...}}`.

## linkedin_post: LinkedIn post: Cero asks before running mutating commands

- Platform: linkedin | Type: post
- Prompt: Write a LinkedIn post for developers explaining how starting a line with '>' in Cero makes a plain-language request, and how Cero shows commands that change things for approval before running them in a real shell. Hook in the first 210 characters, 3 to 5 hashtags at the end, zero emojis, no hype words. Stick strictly to the verified facts.

### Option A

- Chars: 949 | Auto-review: warn (1 warnings)
  - [warn] First line (330 chars) exceeds linkedin visible hook (210 chars).

Most developers type commands. Cero lets you start a line with ">" and describe what you want in plain language. It translates that into shell commands, shows you every command that would change something, waits for your approval, then runs them in a real shell (zsh, bash, or PowerShell on Windows) and returns the actual output.

The model runs locally by default — a 3B parameter model (2.1 GB) or an optional 4B variant — so nothing leaves your machine unless you configure Ollama or an external API. API keys are stored in the system keychain. You can stop a running request with Ctrl+C. Cero learns only when you explicitly use /learn and forgets with /forget. It resolves folder and app names by asking when there are multiple matches.

No hidden execution. No cloud dependency unless you choose one. Just a terminal that shows you what it's about to do before it does it.

#CeroTerminal #DeveloperTools #LocalAI #TerminalWorkflow #OpenSource

Rating linkedin_post / A (1-5): ___

### Option B

- Chars: 1029 | Auto-review: ok (0 warnings)

Most terminals make you remember the exact syntax. In Cero, you start a line with ">" and type what you want in plain language. Here is what happens next.

When a line starts with ">", Cero treats it as a plain-language request instead of a shell command.

It works out the commands needed, then shows any that change things for your approval before running them. Approved commands execute in a real shell — zsh or bash on macOS and Linux, PowerShell on Windows. Cero is a desktop terminal (Tauri v2, Rust, React 19, xterm.js), so those commands run in the same shell environment you already use, not a separate sandbox.

The answer you get back is built from the real output of those commands, not generated from memory. If a request goes off track, Ctrl+C stops it.

Cero v2.2.0 is MIT-licensed and runs on macOS, Linux, and Windows. It also ships with a built-in local model (3B default, runs on your computer), or you can point it at Ollama or an API of your choice.

#Terminal #DeveloperTools #OpenSource #CLI #BuildInPublic

Rating linkedin_post / B (1-5): ___

### Option C

- Chars: 844 | Auto-review: ok (0 warnings)

In Cero, start a line with `>` and you’re writing a plain-language request, not a shell command. Cero works out the commands and shows any that change things for approval before running them in a real shell.

The flow is:

- `>` begins the request.
- Cero determines the commands needed.
- Any command that changes things is shown for approval.
- Approved commands run in zsh or bash, or PowerShell on Windows.
- Cero answers using the real output.

For example: `> create a folder named demo`

The command that creates the folder is shown for approval before it runs. If a request matches multiple folders or apps, Cero asks which one you mean.

Cero is a desktop terminal built with Tauri v2, Rust, React 19, and xterm.js. The `>` syntax is the entry point; the shell remains the execution environment.

#Cero #DeveloperTools #Terminal #Shell

Rating linkedin_post / C (1-5): ___

## x_post: X post: Cero local SLM and system keychain

- Platform: x | Type: post
- Prompt: Write a single X post (280 characters max, max 2 hashtags, zero emojis, no hype words) about Cero running its built-in local model (3B default, 2.1 GB) on your computer or using Ollama/an API of your choice with keys kept in the system keychain. Stick strictly to the verified facts.

### Option A

- Chars: 199 | Auto-review: ok (0 warnings)

Cero runs a 3B local model (2.1 GB) on your machine by default. Also works with Ollama or any API — keys stored in your system keychain. No cloud required unless you choose it. #CeroTerminal #LocalAI

Rating x_post / A (1-5): ___

### Option B

- Chars: 164 | Auto-review: ok (0 warnings)

Cero’s built-in local model (3B default, 2.1 GB) runs on your computer. You can also use Ollama or an API of your choice, with API keys kept in the system keychain.

Rating x_post / B (1-5): ___

### Option C

- Chars: 193 | Auto-review: ok (0 warnings)

Cero runs a built-in local model (3B default, 2.1 GB) on your computer. Prefer something else? Point it at Ollama or an API of your choice — keys stay in your system keychain. #Cero #opensource

Rating x_post / C (1-5): ___

## instagram_caption: Instagram caption: Cero .flow files

- Platform: instagram | Type: post
- Prompt: Write an Instagram caption (strong hook in the first 125 characters, up to 5 hashtags, zero emojis, no hype words) about Cero .flow files: one file, double-click, works on macOS/Linux/Windows, and nothing runs until you click Run. Stick strictly to the verified facts.

### Option A

- Chars: 225 | Auto-review: warn (1 warnings)
  - [warn] First line (166 chars) exceeds instagram visible hook (125 chars).

One file, double-click, runs on macOS Linux Windows. Nothing executes until you click Run. Cero .flow files pick the right install commands for each OS automatically.

#CeroTerminal #dotflow #crossplatform #terminal #devtools

Rating instagram_caption / A (1-5): ___

### Option B

- Chars: 387 | Auto-review: ok (0 warnings)

One .flow file. Double-click it. Cero works on macOS, Linux, and Windows—and nothing runs until you click Run.

Cero picks the right install commands for your OS. The flow is contained in one file, and execution starts only when you click Run.

#CeroTerminal #DeveloperTools #Terminal #OpenSource

Alt text: Cero .flow workflow: one file, double-click to open, then click Run to execute.

Rating instagram_caption / B (1-5): ___

### Option C

- Chars: 524 | Auto-review: ok (0 warnings)

One .flow file. Double-click it on macOS, Linux, or Windows. Nothing runs until you click Run.

A .flow file is a single file you open with a double-click. Cero reads it and picks the right install commands for your operating system, so the same file runs on macOS, Linux, and Windows.

Nothing in the file executes until you click Run. You see what will happen before anything happens.

Cero is a desktop terminal (v2.2.0, MIT) that runs commands in a real shell on your machine.

#Cero #OpenSource #DevTools #Terminal #CLI

Rating instagram_caption / C (1-5): ___
