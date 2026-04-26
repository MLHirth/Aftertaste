use std::{
    net::{TcpStream, ToSocketAddrs},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::Duration,
};

use tauri::{AppHandle, Manager, RunEvent, WindowEvent};

struct BackendState {
    child: Mutex<Option<Child>>,
}

impl BackendState {
    fn new() -> Self {
        Self {
            child: Mutex::new(None),
        }
    }
}

fn socket_addr() -> String {
    let host = std::env::var("AFTERTASTE_API_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port = std::env::var("AFTERTASTE_API_PORT").unwrap_or_else(|_| "8765".to_string());
    format!("{host}:{port}")
}

fn backend_is_reachable() -> bool {
    let addr = socket_addr();
    let resolved = match addr.to_socket_addrs() {
        Ok(mut entries) => entries.next(),
        Err(_) => None,
    };

    if let Some(socket) = resolved {
        return TcpStream::connect_timeout(&socket, Duration::from_millis(400)).is_ok();
    }
    false
}

fn find_project_root() -> Option<PathBuf> {
    if let Ok(custom_root) = std::env::var("AFTERTASTE_ROOT") {
        let candidate = PathBuf::from(custom_root);
        if candidate.join("core/api.py").exists() {
            return Some(candidate);
        }
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    for dir in manifest_dir.ancestors() {
        if dir.join("core/api.py").exists() {
            return Some(dir.to_path_buf());
        }
    }

    if let Ok(current_dir) = std::env::current_dir() {
        for dir in current_dir.ancestors() {
            if dir.join("core/api.py").exists() {
                return Some(dir.to_path_buf());
            }
        }
    }

    None
}

fn python_command(project_root: &Path) -> Command {
    if let Ok(custom_python) = std::env::var("AFTERTASTE_PYTHON_BIN") {
        return Command::new(custom_python);
    }

    let venv_python = project_root.join(".venv/bin/python");
    if venv_python.exists() {
        return Command::new(venv_python);
    }

    Command::new("python3")
}

fn stop_backend(app_handle: &AppHandle) {
    let state = app_handle.state::<BackendState>();
    let mut lock = match state.child.lock() {
        Ok(guard) => guard,
        Err(_) => return,
    };

    if let Some(mut child) = lock.take() {
        log::info!("Stopping managed backend process...");
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn start_backend(app_handle: &AppHandle) -> std::io::Result<()> {
    if backend_is_reachable() {
        log::info!(
            "Backend already reachable at {}. Using existing process.",
            socket_addr()
        );
        return Ok(());
    }

    let Some(project_root) = find_project_root() else {
        return Err(std::io::Error::other(
            "Unable to locate Aftertaste project root (missing core/api.py).",
        ));
    };

    let state = app_handle.state::<BackendState>();
    let mut lock = state
        .child
        .lock()
        .map_err(|_| std::io::Error::other("Backend state lock poisoned."))?;

    if lock.is_some() {
        return Ok(());
    }

    let mut command = python_command(&project_root);
    command
        .arg("-m")
        .arg("core.api")
        .current_dir(project_root)
        .stdin(Stdio::null());

    if cfg!(debug_assertions) {
        command.stdout(Stdio::inherit()).stderr(Stdio::inherit());
    } else {
        command.stdout(Stdio::null()).stderr(Stdio::null());
    }

    let mut child = command.spawn()?;

    thread::sleep(Duration::from_millis(450));
    if let Some(status) = child.try_wait()? {
        return Err(std::io::Error::other(format!(
            "Managed backend exited during startup with status: {status}"
        )));
    }

    *lock = Some(child);
    log::info!("Started managed backend process.");
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(BackendState::new())
        .setup(|app| {
            app.handle().plugin(tauri_plugin_opener::init())?;

            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            if let Err(error) = start_backend(&app.handle()) {
                log::error!(
                    "Failed to start managed backend. App will continue without backend: {}",
                    error
                );
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::CloseRequested { .. })
                && window.app_handle().webview_windows().len() <= 1
            {
                stop_backend(&window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_backend(app_handle);
        }
    });
}
