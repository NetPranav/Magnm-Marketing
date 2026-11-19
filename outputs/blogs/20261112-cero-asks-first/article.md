# Why We Built a Terminal AI That Asks Before Executing

*By Magnm Engineering Team · Verified from Cero v2.2.0 Architecture*

---

Modern developers spend their days in the terminal. As AI coding tools and command generators enter the shell, a dangerous pattern has emerged: tools that autonomously pipe LLM-generated commands directly to execution buffers, or command prompts that obscure the exact commands being run under the hood.

When an AI model hallucinating a flag or an argument can wipe an entire repository or leak credentials, blind autonomous execution is not a feature—it is a catastrophic risk.

This is why we built **Cero** (https://github.com/NetPranav/Cero-Terminal): a modern desktop terminal engineered around a non-negotiable principle: **AI in your terminal, but the call stays yours.**

---

## 1. The Core Problem: The Autonomous Shell Illusion

Piping AI natural language straight into shell execution creates three critical failure modes:
1. **Destructive State Mutations:** A command intended to clean a cache can inadvertently execute an unbounded `rm` or overwrite configuration files.
2. **Hidden System & Network Side-effects:** In background execution, developers have no visibility into whether a tool is spinning up external processes or making outbound calls.
3. **The Loss of Developer Control:** The terminal is the highest-privilege environment on an engineer's machine. Handing root or user shell authority to a probabilistic token generator without review breaks basic system hygiene.

---

## 2. The Architecture of Cero: "Asks First" Execution

Cero is built as a native desktop application using **Tauri v2, Rust, React 19, and xterm.js**. Commands execute in a genuine system shell (`zsh`/`bash` on Linux/macOS, `PowerShell` on Windows).

Here is how plain-language requests work:

### The Plain-Language Prefix (`>`)
In Cero, typing `>` begins an intelligent query:

```bash
> find all node_modules older than 30 days and calculate total disk usage
```

Cero's engine breaks this down into two distinct categories:

### A. Read-Only Questions
If a request is purely observational (reading file sizes, viewing process tables, inspecting git status), Cero answers directly from real command output without interrupting you.

### B. State-Altering Actions: Explicit Approval
If a command touches, modifies, or deletes files, changes network configuration, or modifies system state:
- Cero **stops**.
- It opens an approval dialog displaying the **exact shell command** it wants to run.
- You see the command before anything executes.
- If you hit **Cancel**, nothing runs.
- At any point during execution, standard `Ctrl+C` immediately halts the request.

---

## 3. Local-First: Privacy in the System Keychain

Developer security extends beyond command execution to model privacy and API credentials:
- **Built-in Local Models:** Cero runs a local model right on your device (3B default, 2.1 GB; 4B option). No command history leaves your machine.
- **Ollama & Custom API Support:** For teams preferring existing local Ollama instances or specific hosted models, Cero supports external endpoints.
- **Keychain Security:** API keys are never stored in plain text configuration files; they reside securely in the OS system keychain.

---

## 4. Reproducible Workflows: The `.flow` Format

Sharing multi-step terminal instructions across teams is notoriously fragile. Cero introduces `.flow` files:
- Double-clickable, single-file cross-platform workflows.
- Automatically selects the appropriate OS-level package managers and flags.
- **Strict safety rule:** Nothing in a `.flow` executes automatically upon opening. You inspect the steps and click **Run** to proceed.

---

## 5. What's Next & Getting Involved

Cero is open source under the MIT License. 
- GitHub: [https://github.com/NetPranav/Cero-Terminal](https://github.com/NetPranav/Cero-Terminal)
- Pre-built releases are available for macOS, Linux, and Windows.

We are building developer tools that respect the terminal. Clone it, run it, test it, and let us know what you build.
