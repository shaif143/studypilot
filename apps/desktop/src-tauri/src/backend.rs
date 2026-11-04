//! Supervision of the local Python backend.
//!
//! The backend runs as a Tauri sidecar: a self-contained ARM64 executable
//! bundled inside the `.app`, so the installed application does not depend on
//! a Python interpreter, a virtual environment, Docker, or the user's `PATH`.
//!
//! Three things make the local service safe to run:
//!
//! * **A per-launch secret.** The shell generates a random token and passes it
//!   to the sidecar in its environment. Only the webview can obtain it (via the
//!   `backend_info` command), so another process on the machine that finds the
//!   open port cannot read the student's documents.
//! * **An ephemeral loopback port.** The port is chosen by asking the OS for a
//!   free one and binding to `127.0.0.1`, so the service is never reachable
//!   from the network and never collides with another application.
//! * **Explicit lifecycle.** Startup is confirmed by polling the liveness
//!   endpoint, and the process is killed when the app exits rather than left
//!   running.

use std::net::TcpListener;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use base64::Engine as _;
use rand::RngCore;
use serde::Serialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

/// Name of the bundled executable, matching `externalBin` in `tauri.conf.json`.
const SIDECAR: &str = "studypilot-api";

/// How long to wait for the backend to answer before giving up.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(45);

#[derive(Debug, Clone, Serialize)]
pub struct BackendInfo {
    pub base_url: String,
    pub token: String,
}

/// The running backend, held on the Tauri app state.
pub struct Backend {
    info: BackendInfo,
    child: Mutex<Option<CommandChild>>,
}

impl Backend {
    pub fn info(&self) -> BackendInfo {
        self.info.clone()
    }

    /// Terminate the sidecar. Safe to call more than once.
    pub fn shutdown(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(child) = guard.take() {
                // `kill` sends SIGKILL; the backend holds no un-flushed state
                // because SQLite commits per request in WAL mode.
                let _ = child.kill();
            }
        }
    }
}

#[derive(Debug)]
pub enum StartError {
    NoPort(String),
    Spawn(String),
    Timeout(String),
}

impl std::fmt::Display for StartError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NoPort(detail) => {
                write!(formatter, "Could not reserve a local port: {detail}")
            }
            Self::Spawn(detail) => write!(
                formatter,
                "The bundled StudyPilot engine could not be started: {detail}"
            ),
            Self::Timeout(detail) => write!(
                formatter,
                "The StudyPilot engine did not finish starting: {detail}"
            ),
        }
    }
}

/// Generate a 32-byte URL-safe token for this launch.
fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

/// Ask the OS for a free loopback port.
///
/// There is an unavoidable race between releasing the listener and the backend
/// binding it; it is small, and a bind failure surfaces as a startup timeout
/// with a clear message rather than a silent hang.
fn reserve_port() -> Result<u16, StartError> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|error| StartError::NoPort(error.to_string()))?;
    let port = listener
        .local_addr()
        .map_err(|error| StartError::NoPort(error.to_string()))?
        .port();
    drop(listener);
    Ok(port)
}

/// Poll the unauthenticated liveness endpoint until the backend answers.
fn await_ready(base_url: &str) -> Result<(), StartError> {
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    let url = format!("{base_url}/healthz");
    let mut last_error = String::from("no response");

    while Instant::now() < deadline {
        match ureq::get(&url).timeout(Duration::from_secs(2)).call() {
            Ok(response) if response.status() == 200 => return Ok(()),
            Ok(response) => last_error = format!("status {}", response.status()),
            Err(error) => last_error = error.to_string(),
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Err(StartError::Timeout(last_error))
}

/// Start the sidecar and wait for it to become ready.
pub fn start(app: &AppHandle) -> Result<Backend, StartError> {
    let port = reserve_port()?;
    let token = generate_token();
    let base_url = format!("http://127.0.0.1:{port}");

    // The backend writes the database and uploaded files here. Using Tauri's
    // resolver keeps it inside the sandboxed application-support directory.
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| StartError::Spawn(format!("no application data directory: {error}")))?;
    if let Err(error) = std::fs::create_dir_all(&data_dir) {
        return Err(StartError::Spawn(format!(
            "could not create {}: {error}",
            data_dir.display()
        )));
    }

    let command = app
        .shell()
        .sidecar(SIDECAR)
        .map_err(|error| StartError::Spawn(error.to_string()))?
        // `env` replaces the inherited environment for these keys only, so the
        // backend does not depend on the user's shell configuration.
        .env("STUDYPILOT_PORT", port.to_string())
        .env("STUDYPILOT_HOST", "127.0.0.1")
        .env("STUDYPILOT_API_TOKEN", token.clone())
        .env("STUDYPILOT_DATA_DIR", data_dir.to_string_lossy().to_string())
        .env("STUDYPILOT_LOG_LEVEL", "INFO")
        // The sidecar watches this pid and exits when the shell does. Killing
        // the child is not enough on its own: a one-file PyInstaller build
        // forks a worker, which would otherwise survive as an orphan holding
        // the port and the student's database.
        .env("STUDYPILOT_PARENT_PID", std::process::id().to_string());

    let (mut rx, child) = command
        .spawn()
        .map_err(|error| StartError::Spawn(error.to_string()))?;

    // Drain the sidecar's output so its pipe cannot fill and block it, and so
    // its structured logs reach the shell's stderr for diagnosis.
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    eprint!("[studypilot-api] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Error(error) => eprintln!("[studypilot-api] error: {error}"),
                CommandEvent::Terminated(status) => {
                    eprintln!("[studypilot-api] exited: {status:?}");
                    break;
                }
                _ => {}
            }
        }
    });

    let backend = Backend {
        info: BackendInfo {
            base_url: base_url.clone(),
            token,
        },
        child: Mutex::new(Some(child)),
    };

    if let Err(error) = await_ready(&base_url) {
        // Do not leave an orphaned process behind on a failed start.
        backend.shutdown();
        return Err(error);
    }

    Ok(backend)
}
