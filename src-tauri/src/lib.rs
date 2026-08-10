use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const PROTOCOL_VERSION: u32 = 1;
const SIDECAR_NAME: &str = "copyright-agent-sidecar";

#[derive(Default)]
struct SidecarState {
    session: Mutex<Option<SidecarSession>>,
}

struct SidecarSession {
    connection: SidecarConnection,
    child: Option<CommandChild>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct SidecarConnection {
    base_url: String,
    session_token: String,
    pid: u32,
    version: String,
}

#[derive(Deserialize)]
struct SidecarHandshake {
    event: String,
    host: String,
    port: u16,
    protocol_version: u32,
    version: String,
    pid: u32,
}

#[tauri::command]
async fn start_sidecar(
    app: tauri::AppHandle,
    state: State<'_, SidecarState>,
) -> Result<SidecarConnection, String> {
    if let Some(session) = state
        .session
        .lock()
        .map_err(|_| "sidecar state poisoned")?
        .as_ref()
    {
        return Ok(session.connection.clone());
    }
    let token = random_token();
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    std::fs::create_dir_all(&data_dir).map_err(|error| error.to_string())?;
    let data_dir_arg = data_dir.to_string_lossy().into_owned();
    let command = app
        .shell()
        .sidecar(SIDECAR_NAME)
        .map_err(|error| error.to_string())?
        .args(["--data-dir", data_dir_arg.as_str()])
        .env("COPYRIGHT_AGENT_SESSION_TOKEN", &token);
    let (mut events, child) = command.spawn().map_err(|error| error.to_string())?;
    let line = tokio::time::timeout(Duration::from_secs(12), async {
        while let Some(event) = events.recv().await {
            if let CommandEvent::Stdout(bytes) = event {
                return Some(String::from_utf8_lossy(&bytes).trim().to_owned());
            }
            if let CommandEvent::Error(message) = event {
                return Some(message);
            }
        }
        None
    })
    .await
    .map_err(|_| "sidecar handshake timed out")?
    .ok_or("sidecar exited before handshake")?;
    let handshake: SidecarHandshake =
        serde_json::from_str(&line).map_err(|_| "invalid sidecar handshake")?;
    validate_handshake(&handshake)?;
    let base_url = format!("http://127.0.0.1:{}", handshake.port);
    let client = reqwest::Client::new();
    let mut healthy = false;
    for _ in 0..30 {
        if let Ok(response) = client
            .get(format!("{base_url}/api/v1/health"))
            .header("X-Session-Token", &token)
            .timeout(Duration::from_secs(1))
            .send()
            .await
        {
            if response.status().is_success() {
                healthy = true;
                break;
            }
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    if !healthy {
        let _ = child.kill();
        return Err("sidecar health check failed".into());
    }
    let connection = SidecarConnection {
        base_url,
        session_token: token,
        pid: handshake.pid,
        version: handshake.version,
    };
    eprintln!(
        "sidecar ready: pid={} address={} version={}",
        connection.pid, connection.base_url, connection.version
    );
    *state.session.lock().map_err(|_| "sidecar state poisoned")? = Some(SidecarSession {
        connection: connection.clone(),
        child: Some(child),
    });
    tauri::async_runtime::spawn(async move { while events.recv().await.is_some() {} });
    Ok(connection)
}

fn validate_handshake(value: &SidecarHandshake) -> Result<(), String> {
    if value.event != "sidecar.ready"
        || value.host != "127.0.0.1"
        || value.port == 0
        || value.protocol_version != PROTOCOL_VERSION
        || value.version != env!("CARGO_PKG_VERSION")
    {
        return Err("sidecar handshake validation failed".into());
    }
    Ok(())
}

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

impl Drop for SidecarSession {
    fn drop(&mut self) {
        if let Some(child) = self.child.take() {
            eprintln!("stopping sidecar: pid={}", self.connection.pid);
            let _ = child.kill();
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState::default())
        .invoke_handler(tauri::generate_handler![start_sidecar])
        .run(tauri::generate_context!())
        .expect("error while running desktop application");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_handshake() -> SidecarHandshake {
        SidecarHandshake {
            event: "sidecar.ready".into(),
            host: "127.0.0.1".into(),
            port: 49152,
            protocol_version: PROTOCOL_VERSION,
            version: env!("CARGO_PKG_VERSION").into(),
            pid: 42,
        }
    }

    #[test]
    fn session_token_is_256_bit_lowercase_hex() {
        let token = random_token();
        assert_eq!(token.len(), 64);
        assert!(token.chars().all(|character| character.is_ascii_hexdigit()));
        assert_eq!(token, token.to_ascii_lowercase());
    }

    #[test]
    fn valid_loopback_handshake_is_accepted() {
        assert!(validate_handshake(&valid_handshake()).is_ok());
    }

    #[test]
    fn non_loopback_or_wrong_protocol_handshake_is_rejected() {
        let mut handshake = valid_handshake();
        handshake.host = "0.0.0.0".into();
        assert!(validate_handshake(&handshake).is_err());
        handshake.host = "127.0.0.1".into();
        handshake.protocol_version += 1;
        assert!(validate_handshake(&handshake).is_err());
    }
}
