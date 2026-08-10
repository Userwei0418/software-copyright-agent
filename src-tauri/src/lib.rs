use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use std::fs;
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const PROTOCOL_VERSION: u32 = 1;
const SIDECAR_NAME: &str = "copyright-agent-sidecar";
const KEYRING_SERVICE: &str = "com.local.software-copyright-agent.model-api";

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

#[derive(Deserialize)]
struct SourceMaterialsSnapshot {
    source_document: Option<SourceDocumentArtifact>,
}

#[derive(Deserialize)]
struct SourceDocumentArtifact {
    artifact_relative_path: String,
    integrity: ArtifactIntegrity,
}

#[derive(Deserialize)]
struct ArtifactIntegrity {
    status: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ModelProbeRequest {
    config_id: String,
    protocol_id: String,
    base_url: String,
    model_name: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ModelProbeResult {
    available: bool,
    model_found: bool,
    discovered_models: Vec<String>,
}

#[tauri::command]
async fn start_sidecar(
    app: tauri::AppHandle,
    state: State<'_, SidecarState>,
) -> Result<SidecarConnection, String> {
    let existing = state
        .session
        .lock()
        .map_err(|_| "sidecar state poisoned")?
        .as_ref()
        .map(|session| session.connection.clone());
    if let Some(connection) = existing {
        let healthy = reqwest::Client::new()
            .get(format!("{}/api/v1/health", connection.base_url))
            .header("X-Session-Token", &connection.session_token)
            .timeout(Duration::from_secs(1))
            .send().await.map(|response| response.status().is_success()).unwrap_or(false);
        if healthy { return Ok(connection); }
        if let Some(mut stale) = state.session.lock().map_err(|_| "sidecar state poisoned")?.take() {
            if let Some(child) = stale.child.take() { let _ = child.kill(); }
        }
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

#[tauri::command]
async fn reveal_source_document(
    task_id: String,
    app: tauri::AppHandle,
    state: State<'_, SidecarState>,
) -> Result<(), String> {
    validate_task_id(&task_id)?;
    let connection = state
        .session
        .lock()
        .map_err(|_| "sidecar state poisoned")?
        .as_ref()
        .map(|session| session.connection.clone())
        .ok_or("local service is not connected")?;
    let response = reqwest::Client::new()
        .get(format!(
            "{}/api/v1/tasks/{}/source-materials",
            connection.base_url, task_id
        ))
        .header("X-Session-Token", connection.session_token)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .map_err(|_| "source document status request failed")?;
    if !response.status().is_success() {
        return Err("source document status is unavailable".into());
    }
    let snapshot: SourceMaterialsSnapshot = response
        .json()
        .await
        .map_err(|_| "invalid source document status")?;
    let document = snapshot
        .source_document
        .ok_or("source document has not been generated")?;
    if document.integrity.status != "verified" {
        return Err("source document integrity verification failed".into());
    }
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    let path = verified_artifact_path(&data_dir, &task_id, &document.artifact_relative_path)?;
    reveal_in_file_manager(&path)
}

#[tauri::command]
async fn export_source_document(
    task_id: String,
    destination: String,
    app: tauri::AppHandle,
    state: State<'_, SidecarState>,
) -> Result<(), String> {
    validate_task_id(&task_id)?;
    let source = resolve_source_document(&task_id, &app, &state).await?;
    let destination = PathBuf::from(destination);
    if destination.extension().and_then(|value| value.to_str())
        .map(|value| value.eq_ignore_ascii_case("docx")) != Some(true) {
        return Err("export destination must use the .docx extension".into());
    }
    fs::copy(source, destination).map_err(|_| "failed to export source document")?;
    Ok(())
}

#[tauri::command]
fn reveal_exported_document(path: String) -> Result<(), String> {
    let path = PathBuf::from(path);
    if !path.is_absolute() || !path.is_file()
        || path.extension().and_then(|value| value.to_str())
            .map(|value| value.eq_ignore_ascii_case("docx")) != Some(true)
    {
        return Err("exported document is unavailable".into());
    }
    reveal_in_file_manager(&path)
}

#[tauri::command]
fn store_model_credential(config_id: String, api_key: String) -> Result<(), String> {
    validate_config_id(&config_id)?;
    if api_key.trim().len() < 8 { return Err("API Key 长度不足".into()); }
    keyring::Entry::new(KEYRING_SERVICE, &config_id)
        .map_err(|_| "无法连接系统安全存储")?
        .set_password(api_key.trim()).map_err(|_| "API Key 保存到系统安全存储失败".to_string())
}

#[tauri::command]
fn delete_model_credential(config_id: String) -> Result<(), String> {
    validate_config_id(&config_id)?;
    let entry = keyring::Entry::new(KEYRING_SERVICE, &config_id)
        .map_err(|_| "无法连接系统安全存储")?;
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err("API Key 删除失败".into()),
    }
}

#[tauri::command]
async fn probe_model_config(request: ModelProbeRequest) -> Result<ModelProbeResult, String> {
    validate_config_id(&request.config_id)?;
    let base = request.base_url.trim_end_matches('/');
    if !base.starts_with("https://") && !base.starts_with("http://127.0.0.1")
        && !base.starts_with("http://localhost") {
        return Err("远程模型地址必须使用 HTTPS；本机 Ollama 可使用 HTTP".into());
    }
    let (url, credential_required) = match request.protocol_id.as_str() {
        "openai_compatible" => (format!("{base}/models"), true),
        "anthropic" => (format!("{base}/models"), true),
        "ollama" => (format!("{base}/api/tags"), false),
        _ => return Err("不支持的模型协议".into()),
    };
    let credential = if credential_required {
        Some(keyring::Entry::new(KEYRING_SERVICE, &request.config_id)
            .map_err(|_| "无法连接系统安全存储")?.get_password()
            .map_err(|_| "未找到该模型的 API Key")?)
    } else { None };
    let client = reqwest::Client::builder().timeout(Duration::from_secs(15)).build()
        .map_err(|_| "无法创建模型连接")?;
    let mut call = client.get(url);
    if let Some(key) = credential.as_ref() {
        call = if request.protocol_id == "anthropic" {
            call.header("x-api-key", key).header("anthropic-version", "2023-06-01")
        } else { call.bearer_auth(key) };
    }
    let response = call.send().await.map_err(|_| "模型服务连接失败")?;
    if !response.status().is_success() {
        return Err(format!("模型服务验证失败（HTTP {}）", response.status().as_u16()));
    }
    let payload: serde_json::Value = response.json().await
        .map_err(|_| "模型服务返回了无法识别的数据")?;
    let items = if request.protocol_id == "ollama" {
        payload.get("models").and_then(|value| value.as_array())
    } else { payload.get("data").and_then(|value| value.as_array()) };
    let discovered_models: Vec<String> = items.into_iter().flatten().filter_map(|item| {
        item.get(if request.protocol_id == "ollama" { "name" } else { "id" })
            .and_then(|value| value.as_str()).map(str::to_owned)
    }).take(100).collect();
    let model_found = discovered_models.iter().any(|value| value == &request.model_name);
    Ok(ModelProbeResult { available: true, model_found, discovered_models })
}

fn validate_config_id(value: &str) -> Result<(), String> {
    if value.len() < 8 || value.len() > 64 || !value.chars().all(
        |character| character.is_ascii_alphanumeric() || character == '-') {
        return Err("invalid model config id".into());
    }
    Ok(())
}

async fn resolve_source_document(
    task_id: &str,
    app: &tauri::AppHandle,
    state: &State<'_, SidecarState>,
) -> Result<PathBuf, String> {
    let connection = state.session.lock().map_err(|_| "sidecar state poisoned")?
        .as_ref().map(|session| session.connection.clone())
        .ok_or("local service is not connected")?;
    let response = reqwest::Client::new().get(format!(
        "{}/api/v1/tasks/{}/source-materials", connection.base_url, task_id))
        .header("X-Session-Token", connection.session_token)
        .timeout(Duration::from_secs(5)).send().await
        .map_err(|_| "source document status request failed")?;
    if !response.status().is_success() { return Err("source document status is unavailable".into()); }
    let snapshot: SourceMaterialsSnapshot = response.json().await
        .map_err(|_| "invalid source document status")?;
    let document = snapshot.source_document.ok_or("source document has not been generated")?;
    if document.integrity.status != "verified" {
        return Err("source document integrity verification failed".into());
    }
    let data_dir = app.path().app_data_dir().map_err(|error| error.to_string())?;
    verified_artifact_path(&data_dir, task_id, &document.artifact_relative_path)
}

fn validate_task_id(task_id: &str) -> Result<(), String> {
    if task_id.len() < 8
        || task_id.len() > 64
        || !task_id
            .chars()
            .all(|value| value.is_ascii_alphanumeric() || value == '-')
    {
        return Err("invalid task id".into());
    }
    Ok(())
}

fn verified_artifact_path(
    data_dir: &Path,
    task_id: &str,
    relative: &str,
) -> Result<PathBuf, String> {
    let relative_path = Path::new(relative);
    if relative_path.is_absolute()
        || relative_path
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err("invalid source document path".into());
    }
    let task_root = data_dir
        .join("tasks")
        .join(task_id)
        .canonicalize()
        .map_err(|_| "task data directory is unavailable")?;
    let artifact = task_root
        .join(relative_path)
        .canonicalize()
        .map_err(|_| "source document is unavailable")?;
    if !artifact.starts_with(&task_root) || !artifact.is_file() {
        return Err("source document path escapes task directory".into());
    }
    Ok(artifact)
}

fn reveal_in_file_manager(path: &Path) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let status = Command::new("open").arg("-R").arg(path).status();
    #[cfg(target_os = "windows")]
    let status = Command::new("explorer")
        .arg(format!("/select,{}", path.display()))
        .status();
    #[cfg(all(unix, not(target_os = "macos")))]
    let status = Command::new("xdg-open")
        .arg(path.parent().ok_or("invalid artifact parent")?)
        .status();
    status
        .map_err(|_| "failed to start file manager")?
        .success()
        .then_some(())
        .ok_or_else(|| "file manager could not reveal source document".into())
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
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState::default())
        .invoke_handler(tauri::generate_handler![
            start_sidecar,
            reveal_source_document,
            export_source_document,
            reveal_exported_document,
            store_model_credential,
            delete_model_credential,
            probe_model_config
        ])
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

    #[test]
    fn task_id_rejects_path_syntax() {
        assert!(validate_task_id("3bb5b0f8-5f18-4bc8-9790-174a823e15b4").is_ok());
        assert!(validate_task_id("../tasks/other").is_err());
        assert!(validate_task_id("task/other").is_err());
    }
}
