//! The macOS application shell.
//!
//! Responsibilities, deliberately kept narrow: start and supervise the Python
//! backend, hand the webview its address and secret, and shut the backend down
//! on exit. All application logic lives in the shared frontend and the backend.
//!
//! Startup is asynchronous. The bundled backend is a self-extracting
//! executable and takes a few seconds on a cold launch, so blocking the setup
//! hook would leave the user looking at an unresponsive window. Instead the
//! window appears straight away and the webview's first `backend_info` call
//! waits for readiness, which is also what lets the frontend show a startup
//! failure rather than an app that silently cannot load anything.

mod backend;

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use backend::{Backend, BackendInfo};
use tauri::{Manager, RunEvent, WindowEvent};

/// How long the webview will wait for the engine before giving up.
const READY_TIMEOUT: Duration = Duration::from_secs(90);
const POLL_INTERVAL: Duration = Duration::from_millis(120);

enum Startup {
    Starting,
    Ready(Backend),
    Failed(String),
}

#[derive(Clone)]
struct BackendState(Arc<Mutex<Startup>>);

impl BackendState {
    fn new() -> Self {
        Self(Arc::new(Mutex::new(Startup::Starting)))
    }

    fn set(&self, startup: Startup) {
        if let Ok(mut guard) = self.0.lock() {
            *guard = startup;
        }
    }

    /// Current state: `Ok(Some(info))` once ready, `Ok(None)` while starting.
    fn poll(&self) -> Result<Option<BackendInfo>, String> {
        match &*self
            .0
            .lock()
            .map_err(|_| "The engine supervisor stopped responding.".to_string())?
        {
            Startup::Starting => Ok(None),
            Startup::Ready(backend) => Ok(Some(backend.info())),
            Startup::Failed(message) => Err(message.clone()),
        }
    }

    fn shutdown(&self) {
        if let Ok(guard) = self.0.lock() {
            if let Startup::Ready(backend) = &*guard {
                backend.shutdown();
            }
        }
    }
}

/// Return the local backend's address and this launch's token.
///
/// Waits for the engine to finish starting. The token exists only in the
/// shell's memory and in the sidecar's environment, so this command is the
/// only way the webview can obtain it.
#[tauri::command]
async fn backend_info(state: tauri::State<'_, BackendState>) -> Result<BackendInfo, String> {
    let deadline = Instant::now() + READY_TIMEOUT;
    loop {
        match state.poll()? {
            Some(info) => return Ok(info),
            None if Instant::now() >= deadline => {
                return Err(
                    "The StudyPilot engine did not finish starting within 90 seconds.".into(),
                )
            }
            None => tokio::time::sleep(POLL_INTERVAL).await,
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![backend_info])
        .setup(|app| {
            let state = BackendState::new();
            app.manage(state.clone());

            let handle = app.handle().clone();
            // Started off the main thread so the window paints immediately.
            std::thread::spawn(move || {
                match backend::start(&handle) {
                    Ok(backend) => state.set(Startup::Ready(backend)),
                    Err(error) => {
                        eprintln!("[studypilot] backend failed to start: {error}");
                        state.set(Startup::Failed(error.to_string()));
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<BackendState>() {
                    state.shutdown();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the StudyPilot application")
        .run(|app, event| {
            // Exit and ExitRequested both fire on quit paths; shutdown is
            // idempotent, so covering both is safe and avoids an orphan.
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                if let Some(state) = app.try_state::<BackendState>() {
                    state.shutdown();
                }
            }
        });
}
