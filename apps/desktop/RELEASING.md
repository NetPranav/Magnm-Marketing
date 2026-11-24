# Releasing Magnm Studio Desktop

Releases are made from the owner's laptop with `scripts/release.py`; CI has no signing secrets. The full guide (the
two signing keys, how to back them up, the release steps and how the apps update) is in
[docs/RELEASING.md](../../docs/RELEASING.md).

In short: `python3 scripts/set_version.py X.Y.Z`, merge to `main`, wait for the **Desktop installers** workflow,
then `python3 scripts/release.py X.Y.Z` and run the `gh release create` command it prints.

The updater public key lives in `apps/desktop/src-tauri/tauri.conf.json` (`plugins.updater.pubkey`). If the matching
private key or its password is lost, installed apps can never update again, so back both up as described there.

Per-OS install and uninstall notes for teammates are in [docs/INSTALL.md](../../docs/INSTALL.md).
