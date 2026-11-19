# Releasing Magnm Studio Desktop

This guide covers signing setup, cutting a release build via GitHub Actions, and installing the app on teammates' machines.

## 1. Generate the updater signing key (one-time)

From `apps/desktop/`, generate an Ed25519 keypair with the Tauri CLI:

```bash
cd apps/desktop
pnpm tauri signer generate -w ~/.tauri/magnm-studio.key
```

Enter a password when prompted (or leave blank, though a password is recommended). Keep `~/.tauri/magnm-studio.key` private and backed up securely; never commit it to git.

The command prints your **public key** and also saves it to `~/.tauri/magnm-studio.key.pub`.

## 2. Configure the public key and GitHub secrets

1. Copy the contents of `~/.tauri/magnm-studio.key.pub` into `apps/desktop/src-tauri/tauri.conf.json` under `plugins.updater.pubkey` (replacing `"REPLACE_WITH_TAURI_UPDATER_PUBKEY"`).
2. In your GitHub repository **Settings -> Secrets and variables -> Actions**, add two repository secrets:
   - `TAURI_SIGNING_PRIVATE_KEY`: the full text contents of `~/.tauri/magnm-studio.key`.
   - `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`: the password you chose when generating the key (leave empty string if none).

Once a real public key is in `tauri.conf.json`, released apps will automatically check `latest.json` in the background on startup and prompt the user to restart when an update is downloaded.

## 3. Cut a release

1. Bump the version in `apps/desktop/package.json`, `apps/desktop/src-tauri/package.json` (if any) / `apps/desktop/src-tauri/Cargo.toml`, and `apps/desktop/src-tauri/tauri.conf.json` (for example `0.1.0`).
2. Commit the version bump to `main`.
3. Create and push a `desktop-v*` tag:

```bash
git tag desktop-v0.1.0
git push origin desktop-v0.1.0
```

GitHub Actions (`.github/workflows/desktop-release.yml`) will build the `magnm-engine` sidecar and Tauri installers for Linux, Windows, macOS Apple Silicon (`arm64`), and macOS Intel (`x86_64`), then create a **draft** GitHub release with `latest.json` and signed updater bundles attached. Review the draft on GitHub and click **Publish release**.

You can also trigger a manual build anytime from the **Actions** tab via `workflow_dispatch`.

## 4. Per-OS install notes for teammates

Because release builds use ad-hoc signing (or unsigned Windows binaries) rather than paid Apple Developer / Authenticode certificates, your OS may show a security prompt the first time you open the app.

### Windows
- Download the `.exe` (NSIS) or `.msi` installer from the GitHub release.
- If Windows SmartScreen says "Windows protected your PC", click **More info** -> **Run anyway**.
- On Windows 11 with **Smart App Control** enabled, unsigned installers may be blocked completely. If that happens, open **Windows Security -> App & browser control -> Smart App Control** and turn it **Off** (note: turning Smart App Control off cannot be re-enabled without resetting Windows).

### macOS
- Download the `.dmg` matching your Mac (`aarch64` / Apple Silicon for M1/M2/M3/M4, or `x64` for Intel) and drag **Magnm Studio.app** to **Applications**.
- If Gatekeeper blocks opening the app ("Magnm Studio cannot be opened because the developer cannot be verified"):
  - Option A: Try to open it once, then open **System Settings -> Privacy & Security**, scroll down, and click **Open Anyway**.
  - Option B: Run in Terminal:
    ```bash
    xattr -dr com.apple.quarantine /Applications/Magnm\ Studio.app
    ```

### Linux
- **Debian / Ubuntu (`.deb`):**
  ```bash
  sudo apt install ./Magnm\ Studio_0.1.0_amd64.deb
  ```
- **AppImage (other distros):**
  ```bash
  chmod +x Magnm\ Studio_0.1.0_amd64.AppImage
  ./Magnm\ Studio_0.1.0_amd64.AppImage
  ```
