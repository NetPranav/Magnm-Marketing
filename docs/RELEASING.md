# Releasing Magnm Studio

One person (the owner) makes releases, from their own laptop, with one command. CI has no secrets: it only builds the
unsigned macOS and Windows bundles, and `scripts/release.py` signs everything locally. Releases live on GitHub
Releases of `NetPranav/Magnm-Marketing` (public), one tag per version, `vX.Y.Z`.

## What one release contains

| File | For |
|---|---|
| `latest.json` | the desktop updater (Tauri v2 format) |
| `magnm-studio_X.Y.Z_amd64.AppImage`, `.deb`, `magnm-studio-X.Y.Z-1.x86_64.rpm` and a `.sig` for each | Linux |
| `magnm-studio-X.Y.Z-1-x86_64.pkg.tar.zst` and its `.sig` | Arch Linux (pacman) |
| `magnm-studio_X.Y.Z_x64-setup.exe`, `magnm-studio_X.Y.Z_x64_en-US.msi` and a `.sig` for each | Windows |
| `magnm-studio_X.Y.Z_aarch64.app.tar.gz`, `magnm-studio_X.Y.Z_x64.app.tar.gz` and a `.sig` for each, plus the `.dmg` disk images | macOS |
| `magnm-studio-X.Y.Z.apk` and `android.json` | Android |

`latest.json` lists the signature and address of every updater file. The Arch package is named in its `extra.pacman`
field. `android.json` is `{version, versionCode, url, sha256, bytes, notes}`.

## One-time setup

You need two signing keys. They never go in the repository, in chat, or in CI.

### The desktop updater key

```bash
cd apps/desktop
pnpm tauri signer generate -w ~/magnm-keys/updater.key
```

Choose a strong password. The command also writes `~/magnm-keys/updater.key.pub`. Put the text of that `.pub` file in
`apps/desktop/src-tauri/tauri.conf.json` as `plugins.updater.pubkey` (replacing `REPLACE_WITH_TAURI_UPDATER_PUBKEY`),
commit it, and release. Until then the app has no updater: Settings > Updates says so, and the release script refuses
to run for real. The key that signs the Arch package is the same updater key.

### The Android release keystore

```bash
keytool -genkeypair -v -keystore ~/magnm-keys/release.jks -alias magnm-studio -keyalg RSA -keysize 2048 -validity 10000
```

### Tell the script where the keys are

Put this in a file outside the repository, readable only by you (`chmod 600`), and `source` it before a release:

```bash
export TAURI_SIGNING_PRIVATE_KEY_PATH=~/magnm-keys/updater.key
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD='...'
export MAGNM_ANDROID_KEYSTORE=~/magnm-keys/release.jks
export MAGNM_ANDROID_KEYSTORE_PASSWORD='...'
export MAGNM_ANDROID_KEY_ALIAS=magnm-studio
# only if the key has its own password: export MAGNM_ANDROID_KEY_PASSWORD='...'
```

An empty updater password is allowed if the key was made without one, but the variable must still be set.

## Back up both keys (do this before the first release)

**If you lose a key, every installed copy can no longer update.** The desktop app has the updater public key built in
and refuses anything signed by another key. Android refuses to update an app with an APK signed by another key.
Everyone would have to uninstall and reinstall by hand (on the phone that deletes the app's data).

1. Copy `updater.key`, `updater.key.pub` and `release.jks` to **two separate offline places** (for example two
   encrypted USB drives kept in different places).
2. Keep the two passwords in a password manager, never next to the key files.
3. **Test a restore**: copy a backup to a temporary folder, sign a scratch file with it
   (`pnpm tauri signer sign -f <copy> <file>`) and list the keystore
   (`keytool -list -keystore <copy>`). Delete the temporary copies afterwards.
4. Never commit `*.key`, `*.jks`, `*.keystore` or the env file (they are in `.gitignore` or outside the repository).

## Making a release

1. Choose the number and write it everywhere (VERSION, `tauri.conf.json`, `Cargo.toml`, the `package.json` files,
   the Android `versionCode` and `versionName`, the PKGBUILD):
   ```bash
   python3 scripts/set_version.py 0.2.0
   ```
   The Android `versionCode` is `major * 10000 + minor * 100 + patch` (0.2.0 is 200), so minor and patch are 0 to 99.
   `python3 scripts/set_version.py --check` lists any file that disagrees; a test does the same.
2. Commit that on a branch, open a pull request, merge it to `main`, and pull `main`.
3. Wait for the **Desktop installers** workflow to finish on that commit (Actions tab). It builds the macOS and
   Windows bundles. Do not skip this: the release script downloads them from that run.
4. Look at the plan without building anything:
   ```bash
   python3 scripts/release.py 0.2.0 --dry-run
   ```
5. Make the release. This builds the Linux AppImage, deb and rpm and the signed Android APK, builds the Arch package
   with `makepkg` (nothing is installed), downloads the macOS and Windows bundles, signs every updater file with
   `tauri signer sign`, writes `latest.json` and `android.json`, and prints the exact `gh release create` command:
   ```bash
   source ~/magnm-keys/release.env
   python3 scripts/release.py 0.2.0 --notes release-notes.md
   ```
   It refuses to start unless the working tree is clean and on `main`, VERSION matches, and the five variables are
   set. Everything goes in `dist-release/v0.2.0/` (not committed). Use `--run-id N` if you want a particular
   workflow run, and `--out DIR` for another folder.
6. Check the folder, then run the printed `gh release create ...` command yourself. (`--publish` runs it for you.)
7. Check it works: install the previous version, open **Settings > Updates** (desktop) and **More > Updates**
   (phone), and update.

The script never publishes unless you pass `--publish`.

## How the apps update

* **Desktop** (Settings > Updates): reads `latest.json` from the latest release, shows the version and notes, and on a
  click downloads the file for this kind of install and checks its signature against the built-in public key. A
  small "Update available" badge appears after a quiet check (at most once a day); nothing is installed without a
  click. After installing, "Restart to finish" restarts the app (the engine keeps running until then).
* **Arch**: the pacman package puts `pacman` in `/usr/share/magnm-studio/install-kind`. The app then downloads the
  `.pkg.tar.zst` named in `extra.pacman`, verifies its signature against the same public key, and runs
  `pkexec pacman -U --noconfirm <file>` (you get the system password prompt).
* **Android** (More > Updates): reads `android.json`, compares `versionCode`, and on a tap downloads the APK from the
  release (only from `github.com/NetPranav/Magnm-Marketing/releases/...` and GitHub's release-asset host), checks its
  size and SHA-256, and opens Android's installer. Android allows it only if the APK is signed with the same key as
  the installed app.

## Notes

* `.github/workflows/desktop-release.yml` (the old tag-triggered release that needed repository secrets) is not used:
  the repository has no signing secrets on purpose. The repository administrator can delete it.
* Never run `gh release create` for a version that already exists. If a release is wrong, delete that release and its
  tag on GitHub, fix it, and make it again with a new patch number rather than reusing the old one.
