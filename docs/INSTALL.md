# Installing and Running Magnm Studio

This guide is for teammates setting up Magnm Studio on Linux, macOS, Windows, and Android.

## Desktop installers

**No desktop installer is published yet.** Automated release builds wait on the Tauri updater signing key and repository secrets to be configured (see [apps/desktop/RELEASING.md](../apps/desktop/RELEASING.md)). Until installers are published, run Magnm Studio from source on your laptop.

---

## Running from source (Linux, macOS, Windows)

### 1. Prerequisites

Install these tools once on your machine:

- **Python 3.12 or newer** (3.12 and 3.14 are tested)
- **Node.js 22 or newer** and **pnpm 12** (`corepack enable` or `npm install -g pnpm@12`)
- **Git** (needed for repository sync in the **Repos** section)
- **FFmpeg** (optional, needed for video rendering and team media previews)
- **Ollama** (optional, needed for local AI and required for confidential products or repos)

### 2. Clone the repository

```bash
git clone https://github.com/NetPranav/Magnm-Marketing.git
cd Magnm-Marketing
```

### 3. Build the web interface

The engine serves the built web interface from `apps/web/dist`. Build it before starting the engine:

```bash
cd apps/web
pnpm install --frozen-lockfile
pnpm build
cd ../..
```

(If `apps/web/dist` is not built, the engine falls back to the basic `/legacy` page.)

### 4. Create the Python virtual environment

You can use the helper script or run the commands for your OS directly.

- **Linux / macOS:**
  ```bash
  python3 -m venv .venv
  .venv/bin/python -m pip install -r engine/requirements.txt
  ```

- **Windows (PowerShell):**
  ```powershell
  py -3 -m venv .venv
  .venv\Scripts\python.exe -m pip install -r engine\requirements.txt
  ```

If you also want Playwright's Chromium browser installed right away, run `python3 scripts/dev_setup.py` (Linux/macOS) or `py -3 scripts/dev_setup.py` (Windows), or install it later from **Settings > Setup check > Install browser**.

#### What each optional Python package enables

The engine can start with only the Python standard library, and fails cleanly with a clear message if an optional library is missing. Installing `engine/requirements.txt` enables:

- `ddgs`: DuckDuckGo web search fallback in **Research** when direct sources return fewer than 3 results.
- `playwright`: Browser-based web research, logged-in social research, and browser rendering steps.
- `pillow`: Rendering brand visual cards in **Create** and generating image thumbnails.
- `cryptography`: Encrypting saved social-research browser sessions and official-API posting tokens on disk.
- `keyring`: Storing the encryption keys for those vaults in your OS keychain (Secret Service on Linux, Keychain on macOS, Credential Manager on Windows).

### 5. Start Magnm Studio

- **Linux / macOS:**
  ```bash
  .venv/bin/python engine/server.py
  ```
- **Windows (PowerShell):**
  ```powershell
  .venv\Scripts\python.exe engine\server.py
  ```

Open `http://127.0.0.1:5050` in your browser.

Started this way, the engine has no access token: other programs running on the same laptop can use it as you. Only run
programs and coding tools you trust while it is running.

Reminders and every schedule run by themselves while the engine is open. Change a schedule's time or interval, or
switch it off, under **Plan > Schedules & timers**. To keep your data in a separate folder, use `engine/main.py`:

```bash
.venv/bin/python engine/main.py --port 5050 --data-dir /path/to/magnm-data
```

---

## First run

Once Magnm Studio opens in your browser, go to **Settings** in the navigation bar:

1. **01 Account**: If your admin has invited your Google account, click **Sign in with Google** to turn on workspace sync and use the team's shared AI keys when available.
2. **03 Keys**: If you have personal API keys (for example `NVIDIA_NIM_API_KEY` or numbered keys `NVIDIA_NIM_API_KEY1` through `NVIDIA_NIM_API_KEY10`), add them under **Add or replace a key** (**Key slot** -> **Key value** -> **Save key**). Never share keys or commit `.env` to git.
3. **04 Setup check**: Click **Run check** to verify Python Runtime, Disk Space, FFmpeg, Chromium / Playwright, Node.js, Git, Ollama Local AI, and NVIDIA NIM API Key. Click **Install browser** if Playwright Chromium is not installed yet.
4. **Repos**: Magnm Studio starts with no repositories. Open **Repos** and click **Add repo** to connect a local folder or a GitHub repository; they are saved in your own `config/repos.json`. Admins also see **Add for the whole team** (GitHub repositories only) and **Share with team** on their own GitHub repos: every member then gets those repositories automatically on their next sync, marked **Team**, and cannot edit or remove them (**Managed by your admin**). In **Create** and **Plan my week**, pick a **Repository** to ground a run on that repository's changes, approved facts and research only; a confidential repository makes the whole run use local AI.
5. **Notifications**: Optionally click **Turn on desktop notifications**, then **Send a test notification** to verify alerts appear in the top-right bell and system notifications.

---

## Android debug APK and phone pairing

The Android app is currently distributed as a **debug APK** for testing only (it is debuggable and signed with a throwaway debug key; see [apps/mobile/README.md](../apps/mobile/README.md)).

### Installing the debug APK

- Download the `magnm-studio-android-debug` artifact from the **Mobile app** job in GitHub Actions, or build it locally from `apps/mobile` (`pnpm install --frozen-lockfile && pnpm android:debug`, which outputs `apps/mobile/android/app/build/outputs/apk/debug/app-debug.apk`).
- Debug builds made on different machines have different signing keys. To switch between them you must uninstall
  first, which deletes the app's data on the phone.
- Install it on your Android phone over USB (`adb install -r apps/mobile/android/app/build/outputs/apk/debug/app-debug.apk`) or copy the APK to the phone and install it when Android prompts.

### Pairing your phone with your laptop

Phone connections work strictly over **Tailscale** (same-Wi-Fi discovery is disabled).

1. Install and sign into **Tailscale** with the same Tailscale account on both your laptop and your Android phone.
2. On your laptop, open **Settings > 10 Phone**.
3. Turn on **Allow phone connections**.
4. Click **Pair a phone** to display the QR code (**Scan with Magnm Mobile**; valid for 5 minutes).
5. Open the Magnm Studio app on your phone and scan the QR code.
6. Back on your laptop under **Pending approval**, click **Approve** for your phone.
7. Paired phones appear under **Paired devices**, where you can click **Revoke** at any time.

### Using the phone while the laptop is away

When you are signed in, the phone can comment on a draft, mark it posted and finish a reminder even when the laptop is off or unreachable. The action shows at once as **Waiting for laptop**, goes to the cloud by itself (with no internet it stays on the phone and is sent later), and your laptop applies it on its next sync, after which the phone shows **Applied**, or **Not applied** with the reason. **More > Phone actions** lists them all. Text that names a confidential product is refused on the phone and never queued (the phone checks it against the product rules bundled with the app plus any the team shared; shared rules can only add), an action kind a laptop does not know is left for a newer laptop, one broken action never blocks the others, confidential drafts and reminders have no actions, and an action is applied only on the laptop that has that draft or reminder. If the laptop is reachable the phone keeps using the direct connection. Queued actions are bound to your account and are wiped when you sign out.

---

## Where your data lives

- **Running `engine/server.py` from source (without `MAGNM_HOME`)**: data is stored inside the repository folder (`state/magnm.db`, `outputs/`, `assets/`, `logs/`, `config/`, and `.env`).
- **Your repositories** are per-user data (`config/repos.json`, never shipped with the app). Older installs lose only the two repositories that earlier versions shipped, once, after a backup in `state/backups/`; everything else you added stays. Team repositories are cached separately in `state/team_repos.json` and cleared when you sign out. Phone actions the laptop has applied are remembered in `state/magnm.db` (table `phone_action_receipts`) so each runs exactly once.
- **Running with `MAGNM_HOME` or `--data-dir`**: data is stored in that directory instead.
- **Desktop app (`studio.magnm.desktop`)**:
  - **Linux**: `$XDG_DATA_HOME/studio.magnm.desktop` (usually `~/.local/share/studio.magnm.desktop`)
  - **macOS**: `~/Library/Application Support/studio.magnm.desktop`
  - **Windows**: `%APPDATA%\studio.magnm.desktop`
  - Desktop API keys live in your OS keychain (`magnm-studio`) rather than `.env`.

---

## Backing up and restoring your data folder

### Back up safely

A backup holds your sign-in session and all your chats and drafts. Keep it as private as a password, and never inside
the repository.

1. **Stop Magnm Studio first** (close the terminal running `engine/server.py` / `engine/main.py` or quit the desktop app) so the SQLite database (`state/magnm.db`) is not being modified while you copy it.
2. Copy these folders and files from your data directory to a safe backup location:
   - `state/` (contains `magnm.db`, team session state, and vaults)
   - `outputs/` (generated drafts, cards, videos, and pitch PDFs)
   - `assets/` (uploaded media)
   - `config/`, `agents/`, `playbooks/` (local config and customizations)
3. If running from source, back up `.env` separately in a secure location (encrypted storage or a password manager). Never upload `.env` to GitHub or share it in chat.

### Restore

1. Stop any running Magnm Studio process.
2. Rename each current folder you are restoring (for example `state` to `state.old`). **Never copy a backup over an
   existing `state/` folder:** a leftover `magnm.db-wal` next to an older `magnm.db` can corrupt the database.
3. Copy the backed-up folders into the data directory (and `.env` if running from source).
4. Start the engine again and check your chats and drafts before deleting the renamed folders.

If you are moving from an older `Magnm-Marketing` folder into a new data directory, you can also use **Settings > 05 Import old folder** (**Import**), which copies `outputs/`, `config/`, `agents/`, and SQLite state without overwriting existing files.

Note: Encrypted social-research sessions, posting tokens, and desktop keychain keys are tied to the OS keychain on the original machine. On a new computer, sign into social research and connect posting accounts again, and re-enter your API keys in **Settings > Keys**.

---

## Updating

1. Stop the running engine.
2. Back up, then pull the latest code. If git refuses because of your local edits, don't discard them: ask for help.
   ```bash
   git pull --ff-only
   ```
3. Update Python packages:
   - Linux/macOS: `.venv/bin/python -m pip install -r engine/requirements.txt`
   - Windows: `.venv\Scripts\python.exe -m pip install -r engine\requirements.txt`
4. Rebuild the web UI:
   ```bash
   cd apps/web
   pnpm install --frozen-lockfile
   pnpm build
   cd ../..
   ```
5. Start the engine again.

---

## Troubleshooting

- **You see the old dashboard instead of the new Studio UI**: `apps/web/dist` has not been built yet. Run `pnpm install --frozen-lockfile && pnpm build` inside `apps/web`, then restart the engine.
- **Missing package errors (Pillow, cryptography, keyring, playwright)**: Make sure you started the engine with `.venv/bin/python` (or `.venv\Scripts\python.exe` on Windows) after installing `engine/requirements.txt`.
- **"Install and connect Tailscale on this laptop first." in Settings > Phone**: Start Tailscale and sign in on your laptop, then toggle **Allow phone connections** again.
- **Port 5050 is already in use**: Stop any existing Magnm engine process, or run `engine/main.py --port 0` (or another free port) instead of `engine/server.py`.
- **AI key alerts in the notification bell**: If you see a rejected-key or cooling-down alert, open **Settings > Keys** to check which key label needs attention, or wait for the rate-limit cooldown to finish.
