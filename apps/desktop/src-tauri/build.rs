fn main() {
    let attributes = tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "list_secrets",
            "set_secret",
            "delete_secret",
            "store_secrets",
            "restart_engine",
            "get_engine_logs",
            "update_status",
            "check_update",
            "install_update",
            "restart_app",
        ]),
    );
    tauri_build::try_build(attributes).expect("failed to run tauri-build");
}
