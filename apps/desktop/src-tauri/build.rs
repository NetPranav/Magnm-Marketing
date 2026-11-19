fn main() {
    let attributes = tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "list_secrets",
            "set_secret",
            "delete_secret",
            "restart_engine",
            "get_engine_logs",
        ]),
    );
    tauri_build::try_build(attributes).expect("failed to run tauri-build");
}
