# Releasing Magnm Studio Desktop

This guide covers signing setup, key backup, cutting a release build via GitHub Actions, and teammate installation notes.

## 1. Generate the updater signing key (one-time)

From `apps/desktop/`, generate an Ed25519 keypair with the Tauri CLI:

```bash
cd apps/desktop
pnpm tauri signer generate -w ~/.tauri/magnm-studio.key
```

Choose a strong password when prompted. Keep `~/.tauri/magnm-studio.key` private and never commit it to git.

The command prints your **public key** and also saves it to `~/.tauri/magnm-studio.key.pub`.

## 2. Back up and test-restore the updater key

Before publishing any release, back up `~/.tauri/magnm-studio.key` and `~/.tauri/magnm-studio.key.pub`:

- **Two offline copies**: Store copies of `~/.tauri/magnm-studio.key` and `~/.tauri/magnm-studio.key.pub` on two separate offline media (for example, two encrypted USB drives stored in different safe locations).
- **Password stored separately**: Keep the key password in a password manager or separate offline record, never in the same folder or drive as the key file backup.
- **Test a restore**: Copy the backed-up key to a temporary path, confirm it matches the original checksum, and verify `pnpm tauri signer sign` (or `TAURI_SIGNING_PRIVATE_KEY`) succeeds with your stored password before deleting the temporary test copy.
- **What is lost without it**: Released desktop apps embed `plugins.updater.pubkey` inside the binary and refuse updates signed by any other key. If the private key or its password is lost, existing installations can never auto-update to future releases and every teammate must manually download and reinstall a new build configured with a replacement keypair.

## 3. Configure the public key and GitHub secrets

1. Copy the contents of `~/.tauri/magnm-studio.key.pub` into `apps/desktop/src-tauri/tauri.conf.json` under `plugins.updater.pubkey` (replacing `"REPLACE_WITH_TAURI_UPDATER_PUBKEY"`).
2. In your GitHub repository **Settings -> Secrets and variables -> Actions**, add two repository secrets:
   - `TAURI_SIGNING_PRIVATE_KEY`: the full text contents of `~/.tauri/magnm-studio.key`.
   - `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`: the password you chose when generating the key.

The release workflow validates early that `TAURI_SIGNING_PRIVATE_KEY` is present and `plugins.updater.pubkey` is not the placeholder string, and smoke-tests the `magnm-engine` sidecar before bundling.

## 4. Cut a release

1. Bump the version in `apps/desktop/package.json`, `apps/desktop/src-tauri/Cargo.toml`, and `apps/desktop/src-tauri/tauri.conf.json` (for example `0.1.0`).
2. Commit the version bump to `main`.
3. Create and push a `desktop-v*` tag:

```bash
git tag desktop-v0.1.0
git push origin desktop-v0.1.0
```

GitHub Actions (`.github/workflows/desktop-release.yml`) builds and smoke-tests the `magnm-engine` sidecar, packages Tauri installers for Linux (`ubuntu-22.04`), Windows (`windows-latest`), macOS Apple Silicon (`macos-15`), and macOS Intel (`macos-15-intel`), and creates a **draft** GitHub release with `latest.json` and signed updater bundles attached. Review the draft on GitHub and click **Publish release**.

You can also trigger a manual build anytime from the **Actions** tab via `workflow_dispatch`.

## 5. Per-OS install notes for teammates

Release builds currently use ad-hoc signing (or unsigned Windows binaries) rather than paid Apple Developer / Authenticode certificates, so your operating system may prompt or block an unverified installer. Never switch off OS security protections (such as Smart App Control or Gatekeeper) as the normal path; if a machine blocks an unsigned installer, run Magnm Studio from source instead (see [docs/INSTALL.md](../../docs/INSTALL.md)).

### Windows
- Download the `.exe` (NSIS) or `.msi` installer from the GitHub release and verify it came from the repository's Releases page.
- If Windows SmartScreen prompts ("Windows protected your PC") and your policy permits reviewed internal tools, inspect the publisher/hash via **More info** before proceeding; on Windows 11 systems enforcing **Smart App Control**, run from source rather than turning Smart App Control off.

### macOS
- Download the `.dmg` matching your Mac (`aarch64` / Apple Silicon for M-series Macs, or `x64` for Intel) and drag **Magnm Studio.app** to **Applications**.
- If Gatekeeper asks to confirm opening the downloaded app, review it through **System Settings -> Privacy & Security** (**Open Anyway** for that single app after checking it came from your team's GitHub release) rather than disabling Gatekeeper system-wide.

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
