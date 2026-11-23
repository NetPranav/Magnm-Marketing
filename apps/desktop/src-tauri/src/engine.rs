//! Process supervisor for the local Magnm Python engine.

use std::collections::VecDeque;
use std::fmt::Write as _;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Deserialize;

use crate::secrets;

const READY_PREFIX: &str = "MAGNM_ENGINE_READY";
const READY_TIMEOUT: Duration = Duration::from_secs(40);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(3);
const LOG_RING_CAPACITY: usize = 200;
const ERROR_LOG_TAIL: usize = 30;

/// Handshake payload emitted on stdout by `engine/main.py`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct EngineReadyInfo {
    pub port: u16,
    pub pid: u32,
    pub data_root: String,
}

/// Where a running engine listens and the token its page uses.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EngineEndpoint {
    pub port: u16,
    pub token: String,
}

/// Result of restarting the engine: its endpoint, and whether the window must navigate to it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RestartOutcome {
    pub endpoint: EngineEndpoint,
    pub navigate: bool,
}

/// The window only has to navigate when the engine is not at the endpoint it already shows.
pub fn restart_needs_navigation(prev: Option<&EngineEndpoint>, new: &EngineEndpoint) -> bool {
    prev != Some(new)
}

/// Restart on the previous port and token so the open page keeps working. Only if that endpoint
/// cannot be started again (the port is taken) fall back to a new port and token, which needs a
/// navigation. `start` receives the endpoint to reuse, or `None` for any port and a new token.
pub fn restart_with<F>(prev: Option<EngineEndpoint>, mut start: F) -> Result<RestartOutcome, String>
where
    F: FnMut(Option<&EngineEndpoint>) -> Result<EngineEndpoint, String>,
{
    if let Some(old) = prev.as_ref() {
        if let Ok(endpoint) = start(Some(old)) {
            let navigate = restart_needs_navigation(prev.as_ref(), &endpoint);
            return Ok(RestartOutcome { endpoint, navigate });
        }
    }
    let endpoint = start(None)?;
    let navigate = restart_needs_navigation(prev.as_ref(), &endpoint);
    Ok(RestartOutcome { endpoint, navigate })
}

#[derive(Debug, Clone)]
struct EngineCommandSpec {
    program: String,
    args: Vec<String>,
}

struct RunningEngine {
    child: Child,
    port: u16,
    token: String,
}

/// Ring buffer holding the most recent engine stderr lines.
#[derive(Clone, Default)]
struct LogBuffer {
    lines: Arc<Mutex<VecDeque<String>>>,
}

impl LogBuffer {
    fn clear(&self) {
        if let Ok(mut guard) = self.lines.lock() {
            guard.clear();
        }
    }

    fn push(&self, line: String) {
        if let Ok(mut guard) = self.lines.lock() {
            if guard.len() >= LOG_RING_CAPACITY {
                guard.pop_front();
            }
            guard.push_back(line);
        }
    }

    fn tail(&self, count: usize) -> Vec<String> {
        let Ok(guard) = self.lines.lock() else {
            return Vec::new();
        };
        let skip = guard.len().saturating_sub(count);
        guard.iter().skip(skip).cloned().collect()
    }
}

/// Manages the lifecycle, logs, and clean shutdown of the local engine process.
pub struct EngineSupervisor {
    data_dir: PathBuf,
    running: Mutex<Option<RunningEngine>>,
    op_lock: Mutex<()>,
    logs: LogBuffer,
    local_base_url: Mutex<Option<String>>,
}

impl EngineSupervisor {
    /// Create a supervisor storing runtime state and logs under `data_dir`.
    pub fn new(data_dir: PathBuf) -> Self {
        Self {
            data_dir,
            running: Mutex::new(None),
            op_lock: Mutex::new(()),
            logs: LogBuffer::default(),
            local_base_url: Mutex::new(None),
        }
    }

    /// Record the initial local origin URL (e.g. `tauri://localhost/`).
    pub fn set_local_base_url(&self, url: String) {
        if let Ok(mut guard) = self.local_base_url.lock() {
            *guard = Some(url);
        }
    }

    /// Return the local origin base URL with a trailing slash.
    pub fn local_base_url(&self) -> String {
        if let Ok(guard) = self.local_base_url.lock() {
            if let Some(url) = guard.as_ref() {
                if url.ends_with('/') {
                    return url.clone();
                }
                return format!("{url}/");
            }
        }
        if cfg!(target_os = "windows") {
            "http://tauri.localhost/".to_string()
        } else {
            "tauri://localhost/".to_string()
        }
    }

    /// Return up to the last `count` lines captured from engine stderr.
    pub fn last_log_lines(&self, count: usize) -> Vec<String> {
        self.logs.tail(count)
    }

    /// Return up to the last 30 lines for display on `error.html`.
    pub fn recent_error_logs(&self) -> Vec<String> {
        self.last_log_lines(ERROR_LOG_TAIL)
    }

    /// Start the engine (stopping any existing instance first) and wait for READY.
    pub fn start(&self) -> Result<(u16, String), String> {
        self.start_with(None).map(|ep| (ep.port, ep.token))
    }

    /// The endpoint of the running engine, if it is up and has reported its port.
    pub fn current_endpoint(&self) -> Option<EngineEndpoint> {
        let guard = self.running.lock().ok()?;
        let eng = guard.as_ref()?;
        (eng.port > 0).then(|| EngineEndpoint {
            port: eng.port,
            token: eng.token.clone(),
        })
    }

    /// Restart the engine on the same port and token when it can, so the page never has to navigate.
    pub fn restart_in_place(&self) -> Result<RestartOutcome, String> {
        let prev = self.current_endpoint();
        restart_with(prev, |preferred| self.start_with(preferred))
    }

    /// Start the engine on the preferred endpoint (or any port with a new token) and wait for READY.
    fn start_with(&self, preferred: Option<&EngineEndpoint>) -> Result<EngineEndpoint, String> {
        let _op_guard = self
            .op_lock
            .lock()
            .map_err(|_| "Engine supervisor lock poisoned".to_string())?;

        self.stop_inner();
        self.logs.clear();

        fs::create_dir_all(&self.data_dir)
            .map_err(|err| format!("Failed to create app data dir: {err}"))?;
        let logs_dir = self.data_dir.join("logs");
        fs::create_dir_all(&logs_dir)
            .map_err(|err| format!("Failed to create logs dir: {err}"))?;

        let log_path = logs_dir.join("engine.log");
        let mut log_file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .map_err(|err| format!("Failed to open {}: {err}", log_path.display()))?;
        let _ = writeln!(log_file, "--- Magnm engine starting ---");

        let spec = resolve_engine_command()?;
        let token = match preferred {
            Some(ep) => ep.token.clone(),
            None => generate_token()?,
        };
        let port_arg = preferred.map_or(0, |ep| ep.port);
        let parent_pid = std::process::id().to_string();
        let secrets_env = secrets::load_secrets_for_env();

        let mut cmd = Command::new(&spec.program);
        cmd.args(&spec.args)
            .arg("--port")
            .arg(port_arg.to_string())
            .arg("--data-dir")
            .arg(&self.data_dir)
            .env("MAGNM_ENGINE_TOKEN", &token)
            .env("MAGNM_PARENT_PID", &parent_pid)
            .env("MAGNM_SECRETS_MODE", "keychain")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        for (key, value) in secrets_env {
            cmd.env(key, value);
        }

        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = cmd.spawn().map_err(|err| {
            format!(
                "Failed to spawn engine command '{}': {err}",
                spec.program
            )
        })?;

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Failed to capture engine stdout".to_string())?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| "Failed to capture engine stderr".to_string())?;

        // Store immediately so app exit can clean up even during startup.
        if let Ok(mut guard) = self.running.lock() {
            *guard = Some(RunningEngine {
                child,
                port: 0,
                token: token.clone(),
            });
        }

        let log_buf = self.logs.clone();
        std::thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line_res in reader.lines() {
                match line_res {
                    Ok(line) => {
                        let _ = writeln!(log_file, "{line}");
                        let _ = log_file.flush();
                        log_buf.push(line);
                    }
                    Err(_) => break,
                }
            }
        });

        let (ready_tx, ready_rx) = mpsc::channel::<Result<EngineReadyInfo, String>>();
        std::thread::spawn(move || {
            let reader = BufReader::new(stdout);
            let mut sent_result = false;
            for line_res in reader.lines() {
                let Ok(line) = line_res else {
                    break;
                };
                if !sent_result {
                    match parse_ready_line(&line) {
                        Ok(Some(info)) => {
                            let _ = ready_tx.send(Ok(info));
                            sent_result = true;
                        }
                        Ok(None) => {}
                        Err(err) => {
                            let _ = ready_tx.send(Err(err));
                            sent_result = true;
                        }
                    }
                }
            }
            if !sent_result {
                let _ = ready_tx.send(Err(
                    "Engine exited before reporting MAGNM_ENGINE_READY".to_string()
                ));
            }
        });

        let ready_info = match ready_rx.recv_timeout(READY_TIMEOUT) {
            Ok(Ok(info)) => info,
            Ok(Err(err)) => {
                std::thread::sleep(Duration::from_millis(75));
                self.stop_inner();
                return Err(err);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.stop_inner();
                return Err(format!(
                    "Engine did not become ready within {} seconds",
                    READY_TIMEOUT.as_secs()
                ));
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                std::thread::sleep(Duration::from_millis(75));
                self.stop_inner();
                return Err("Engine stdout channel closed unexpectedly".to_string());
            }
        };

        if let Ok(mut guard) = self.running.lock() {
            if let Some(ref mut eng) = *guard {
                if eng.token == token {
                    eng.port = ready_info.port;
                    return Ok(EngineEndpoint {
                        port: ready_info.port,
                        token,
                    });
                }
            }
        }

        self.stop_inner();
        Err("Engine was stopped while starting".to_string())
    }

    /// Gracefully stop the running engine, killing it if it does not exit in 3 s.
    pub fn stop(&self) {
        self.stop_inner();
    }

    fn stop_inner(&self) {
        let taken = match self.running.lock() {
            Ok(mut guard) => guard.take(),
            Err(poisoned) => poisoned.into_inner().take(),
        };

        let Some(mut eng) = taken else {
            return;
        };

        if eng.port > 0 {
            send_shutdown_request(eng.port, &eng.token);
            let deadline = Instant::now() + SHUTDOWN_TIMEOUT;
            while Instant::now() < deadline {
                match eng.child.try_wait() {
                    Ok(Some(_)) => return,
                    Ok(None) => std::thread::sleep(Duration::from_millis(50)),
                    Err(_) => break,
                }
            }
        }

        let _ = eng.child.kill();
        let _ = eng.child.wait();
    }
}

impl Drop for EngineSupervisor {
    fn drop(&mut self) {
        self.stop_inner();
    }
}

/// Parse a stdout line from the engine for the `MAGNM_ENGINE_READY` handshake.
///
/// Returns `Ok(None)` for unrelated noise lines, `Ok(Some(info))` for a valid
/// handshake line, and `Err(...)` if a `MAGNM_ENGINE_READY` line has malformed
/// or incomplete JSON.
pub fn parse_ready_line(line: &str) -> Result<Option<EngineReadyInfo>, String> {
    let trimmed = line.trim();
    let Some(rest) = trimmed.strip_prefix(READY_PREFIX) else {
        return Ok(None);
    };
    if !rest.is_empty() && !rest.starts_with(char::is_whitespace) {
        return Ok(None);
    }

    let json_payload = rest.trim();
    if json_payload.is_empty() {
        return Err("Malformed MAGNM_ENGINE_READY line: missing JSON payload".to_string());
    }

    let info: EngineReadyInfo = serde_json::from_str(json_payload)
        .map_err(|err| format!("Malformed MAGNM_ENGINE_READY JSON: {err}"))?;
    if info.port == 0 {
        return Err("Malformed MAGNM_ENGINE_READY payload: port must be > 0".to_string());
    }

    Ok(Some(info))
}

fn generate_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes)
        .map_err(|err| format!("Failed to generate engine token: {err}"))?;
    let mut token = String::with_capacity(64);
    for byte in bytes {
        let _ = write!(&mut token, "{byte:02x}");
    }
    Ok(token)
}

#[cfg(debug_assertions)]
fn resolve_dev_python_program(repo_root: &std::path::Path) -> String {
    let clean_root = repo_root
        .canonicalize()
        .unwrap_or_else(|_| repo_root.to_path_buf());
    let venv_python = if cfg!(windows) {
        clean_root.join(".venv").join("Scripts").join("python.exe")
    } else {
        clean_root.join(".venv").join("bin").join("python")
    };
    if venv_python.is_file() {
        venv_python.to_string_lossy().into_owned()
    } else {
        "python3".to_string()
    }
}

fn resolve_engine_command() -> Result<EngineCommandSpec, String> {
    #[cfg(debug_assertions)]
    {
        if let Ok(override_cmd) = std::env::var("MAGNM_ENGINE_CMD") {
            let trimmed = override_cmd.trim();
            if !trimmed.is_empty() {
                let parts: Vec<String> = trimmed.split_whitespace().map(str::to_string).collect();
                if parts.len() == 1 && parts[0].ends_with(".py") {
                    return Ok(EngineCommandSpec {
                        program: "python3".to_string(),
                        args: parts,
                    });
                }
                return Ok(EngineCommandSpec {
                    program: parts[0].clone(),
                    args: parts[1..].to_vec(),
                });
            }
        }

        let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("..");
        let raw_main_py = repo_root.join("engine").join("main.py");
        let main_py = raw_main_py.canonicalize().unwrap_or(raw_main_py);
        if !main_py.is_file() {
            return Err(format!(
                "Engine entrypoint not found at {}",
                main_py.display()
            ));
        }

        Ok(EngineCommandSpec {
            program: resolve_dev_python_program(&repo_root),
            args: vec![main_py.to_string_lossy().into_owned()],
        })
    }

    #[cfg(not(debug_assertions))]
    {
        let exe = std::env::current_exe()
            .map_err(|err| format!("Failed to locate application executable: {err}"))?;
        let exe_dir = exe
            .parent()
            .ok_or_else(|| "Application executable has no parent directory".to_string())?;
        let bin_name = if cfg!(windows) {
            "magnm-engine.exe"
        } else {
            "magnm-engine"
        };
        let sidecar = exe_dir.join(bin_name);
        if !sidecar.is_file() {
            return Err(format!(
                "Engine sidecar binary '{}' was not found next to the application executable ({}).",
                bin_name,
                sidecar.display()
            ));
        }
        Ok(EngineCommandSpec {
            program: sidecar.to_string_lossy().into_owned(),
            args: Vec::new(),
        })
    }
}

fn send_shutdown_request(port: u16, token: &str) {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let timeout = Duration::from_millis(1500);
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, timeout) else {
        return;
    };
    let _ = stream.set_write_timeout(Some(timeout));
    let _ = stream.set_read_timeout(Some(timeout));

    let request = format!(
        "POST /api/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Magnm-Token: {token}\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}"
    );
    if stream.write_all(request.as_bytes()).is_ok() {
        let _ = stream.flush();
        let mut buf = [0u8; 256];
        let _ = stream.read(&mut buf);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn endpoint(port: u16, token: &str) -> EngineEndpoint {
        EngineEndpoint {
            port,
            token: token.to_string(),
        }
    }

    #[test]
    fn restart_keeps_the_same_port_and_token_without_navigating() {
        let old = endpoint(43123, "tok-old");
        let mut asked = Vec::new();
        let out = restart_with(Some(old.clone()), |pref| {
            asked.push(pref.cloned());
            Ok(pref.cloned().expect("must ask for the old endpoint first"))
        })
        .unwrap();
        assert_eq!(out.endpoint, old);
        assert!(!out.navigate);
        assert_eq!(asked, vec![Some(old)]);
    }

    #[test]
    fn restart_falls_back_to_a_new_port_only_when_the_old_one_cannot_be_bound() {
        let old = endpoint(43123, "tok-old");
        let fresh = endpoint(50001, "tok-new");
        let mut asked = Vec::new();
        let out = restart_with(Some(old.clone()), |pref| {
            asked.push(pref.cloned());
            match pref {
                Some(_) => Err("address already in use".to_string()),
                None => Ok(fresh.clone()),
            }
        })
        .unwrap();
        assert_eq!(out.endpoint, fresh);
        assert!(out.navigate, "a new port and token needs a navigation");
        assert_eq!(asked, vec![Some(old), None]);
    }

    #[test]
    fn restart_after_the_engine_was_down_navigates_to_it() {
        let fresh = endpoint(50002, "tok-new");
        let out = restart_with(None, |pref| {
            assert!(pref.is_none());
            Ok(fresh.clone())
        })
        .unwrap();
        assert!(out.navigate, "the window shows the error page, not the engine");
    }

    #[test]
    fn restart_reports_the_error_when_nothing_can_start() {
        let err = restart_with(Some(endpoint(43123, "t")), |_| Err("no engine".to_string())).unwrap_err();
        assert_eq!(err, "no engine");
    }

    #[test]
    fn navigation_is_needed_only_when_the_endpoint_changed() {
        let a = endpoint(1000, "a");
        assert!(!restart_needs_navigation(Some(&a), &endpoint(1000, "a")));
        assert!(restart_needs_navigation(Some(&a), &endpoint(1001, "a")));
        assert!(restart_needs_navigation(Some(&a), &endpoint(1000, "b")));
        assert!(restart_needs_navigation(None, &a));
    }

    #[test]
    fn parse_ready_line_ignores_noise_lines() {
        assert_eq!(parse_ready_line("").unwrap(), None);
        assert_eq!(parse_ready_line("INFO: initializing gateway").unwrap(), None);
        assert_eq!(
            parse_ready_line("debug note mentioning MAGNM_ENGINE_READY mid-line").unwrap(),
            None
        );
        assert_eq!(
            parse_ready_line("MAGNM_ENGINE_READY_OTHER {\"port\": 1}").unwrap(),
            None
        );
    }

    #[test]
    fn parse_ready_line_parses_valid_handshake() {
        let line = r#"MAGNM_ENGINE_READY {"port": 43123, "pid": 9876, "data_root": "/tmp/magnm"}"#;
        let info = parse_ready_line(line)
            .expect("valid line should parse")
            .expect("should return Some");
        assert_eq!(info.port, 43123);
        assert_eq!(info.pid, 9876);
        assert_eq!(info.data_root, "/tmp/magnm");

        // Trailing newline/whitespace is handled.
        let padded = "  MAGNM_ENGINE_READY {\"port\": 8080, \"pid\": 1, \"data_root\": \"C:\\\\data\"} \r\n";
        let info2 = parse_ready_line(padded).unwrap().unwrap();
        assert_eq!(info2.port, 8080);
    }

    #[test]
    fn parse_ready_line_rejects_malformed_json_and_zero_port() {
        assert!(parse_ready_line("MAGNM_ENGINE_READY").is_err());
        assert!(parse_ready_line("MAGNM_ENGINE_READY {not valid json").is_err());
        assert!(parse_ready_line("MAGNM_ENGINE_READY {\"port\": \"abc\"}").is_err());
        assert!(
            parse_ready_line(r#"MAGNM_ENGINE_READY {"port": 0, "pid": 1, "data_root": "/tmp"}"#)
                .is_err()
        );
    }

    #[cfg(debug_assertions)]
    #[test]
    fn resolve_dev_python_prefers_venv_when_present() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let tmp_root = std::env::temp_dir().join(format!("magnm_engine_rs_{nanos}"));
        fs::create_dir_all(&tmp_root).unwrap();

        assert_eq!(resolve_dev_python_program(&tmp_root), "python3");

        let venv_py = if cfg!(windows) {
            tmp_root.join(".venv").join("Scripts").join("python.exe")
        } else {
            tmp_root.join(".venv").join("bin").join("python")
        };
        fs::create_dir_all(venv_py.parent().unwrap()).unwrap();
        fs::write(&venv_py, b"").unwrap();

        let resolved = resolve_dev_python_program(&tmp_root);
        assert!(
            resolved.ends_with(if cfg!(windows) { "python.exe" } else { "python" })
                && resolved.contains(".venv"),
            "expected .venv python path, got {resolved}"
        );

        let _ = fs::remove_dir_all(&tmp_root);
    }
}
