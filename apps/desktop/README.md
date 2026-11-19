# Magnm Studio Desktop

Tauri 2 desktop shell for Magnm Studio. It displays a minimal startup splash page while launching and supervising the local Python engine (`engine/main.py` in dev, `magnm-engine` sidecar in release), manages API keys in the OS keychain, and loads the engine web UI on `http://127.0.0.1:<port>`.

## Development

Prerequisites: Node.js, `pnpm`, Rust (`cargo`), and platform WebView dependencies (on Linux: `webkit2gtk-4.1`, `libsoup3`, `gtk3`).

```bash
cd apps/desktop
pnpm install
pnpm dev
```

In debug builds, the app spawns `python3 <repo>/engine/main.py --port 0 --data-dir <app_data_dir>`. You can override the engine command in dev with `MAGNM_ENGINE_CMD`.

To run Rust checks and unit tests:

```bash
cd apps/desktop/src-tauri
cargo check
cargo clippy
cargo test
```

## Secrets and OS Keychain

API keys are stored in the OS keychain under the service name `magnm-studio` using `keyring` (Secret Service on Linux, Keychain on macOS, Credential Manager on Windows). On engine startup, stored keys are injected into the engine process environment alongside `MAGNM_SECRETS_MODE=keychain`.

Exposed Tauri commands (available via `window.__TAURI__.core.invoke`):
- `list_secrets()` -> `Array<{ name, is_set }>` (never returns secret values)
- `set_secret(name, value)` -> validates `name` against the allow-list, saves it, restarts the engine, and navigates to the new URL
- `delete_secret(name)` -> validates `name` against the allow-list, removes it, restarts the engine, and navigates to the new URL
- `restart_engine()` -> restarts the engine and navigates to the new URL

If no secret service is available on Linux, the app still starts: `list_secrets()` returns `is_set: false` for all keys and `set_secret()` returns a clear error.

## Data Directory

Runtime state, SQLite data, and engine logs (`logs/engine.log`) live inside Tauri's `app_data_dir()` for identifier `studio.magnm.desktop`:
- **Linux:** `$XDG_DATA_HOME/studio.magnm.desktop` (usually `~/.local/share/studio.magnm.desktop`)
- **macOS:** `~/Library/Application Support/studio.magnm.desktop`
- **Windows:** `%APPDATA%\studio.magnm.desktop`

## Release Builds

Release builds (`pnpm build`) expect a standalone engine binary named `magnm-engine` (`magnm-engine.exe` on Windows) placed next to the desktop executable (built with PyInstaller in a later step). If the sidecar binary is missing at launch, the app navigates to `error.html` with a clear message.
