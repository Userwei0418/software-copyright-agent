use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::ffi::OsString;
use std::fs;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const PROTOCOL_VERSION: u32 = 1;
const SIDECAR_NAME: &str = "copyright-agent-sidecar";

#[derive(Default)]
struct SidecarState {
    session: Mutex<Option<SidecarSession>>,
    startup: tokio::sync::Mutex<()>,
}

#[derive(Default)]
struct ProjectCaptureState {
    sessions: Mutex<HashMap<String, ProjectCaptureSession>>,
}

struct ProjectCaptureSession {
    children: Vec<Child>,
    started_at: Instant,
    target_url: String,
    command_preview: String,
    log_path: PathBuf,
}

struct SidecarSession {
    connection: SidecarConnection,
    child: Option<CommandChild>,
}

fn runtime_data_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("COPYRIGHT_AGENT_DATA_DIR") {
        let path = PathBuf::from(path);
        if !path.as_os_str().is_empty() {
            return Ok(path);
        }
    }
    app.path().app_data_dir().map_err(|error| error.to_string())
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
    quality: ArtifactQuality,
}

#[derive(Deserialize)]
struct ArtifactQuality {
    status: String,
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
    normalized_base_url: String,
    discovery_source: String,
    warning: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ModelConnectionTestResult {
    ok: bool,
    elapsed_ms: u128,
    endpoint_mode: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ManualExportResult {
    destination_path: String,
    size_bytes: u64,
    sha256: String,
    verified: bool,
    receipt_recorded: bool,
}

#[derive(Serialize)]
struct ManualExportReceiptRequest {
    document_version: u32,
    export_kind: String,
    destination_path: String,
    size_bytes: u64,
    sha256: String,
}

#[derive(Clone, Deserialize)]
struct CaptureLaunchCandidate {
    id: String,
    program: String,
    args: Vec<String>,
    working_directory: String,
    command_preview: String,
    default_url: String,
    #[serde(default)]
    services: Vec<CaptureLaunchService>,
}

#[derive(Clone, Deserialize)]
struct CaptureLaunchService {
    program: String,
    args: Vec<String>,
    working_directory: String,
    command_preview: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ProjectCaptureStatus {
    job_id: String,
    status: String,
    pid: u32,
    target_url: String,
    command_preview: String,
    elapsed_seconds: u64,
    log_tail: String,
    exit_code: Option<i32>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ProjectPageCaptureResult {
    path: String,
    url: String,
    width: u32,
    height: u32,
    browser: String,
}

const SENSEAUDIO_MODELS: &[&str] = &[
    "senseaudio-s2",
    "senseaudio-s2-flash",
    "senseaudio-s2-lite",
    "senseaudio-s1",
    "senseaudio-vl-1.0-260319",
    "senseaudio-vl-lite-1.0-260319",
    "sensenova-6.7-flash-lite",
    "deepseek-v4-flash-0731",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "qwen3.6-27b",
    "qwen3.6-35b-a3b",
    "kimi-k2.6",
    "glm-5.1",
    "glm-5.2",
    "minimax-m2.7",
    "doubao-seed-2-0-pro-260215",
];

#[tauri::command]
async fn start_sidecar(
    app: tauri::AppHandle,
    state: State<'_, SidecarState>,
) -> Result<SidecarConnection, String> {
    // Hot reload and automatic fetch recovery can invoke this command together.
    // Serialize the complete handshake so only one frozen sidecar can be born.
    let _startup_guard = state.startup.lock().await;
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
            .send()
            .await
            .map(|response| response.status().is_success())
            .unwrap_or(false);
        if healthy {
            return Ok(connection);
        }
        if let Some(mut stale) = state
            .session
            .lock()
            .map_err(|_| "sidecar state poisoned")?
            .take()
        {
            if let Some(child) = stale.child.take() {
                let _ = child.kill();
            }
        }
    }
    let token = random_token();
    let data_dir = runtime_data_dir(&app)?;
    std::fs::create_dir_all(&data_dir).map_err(|error| error.to_string())?;
    let data_dir_arg = data_dir.to_string_lossy().into_owned();
    let command = app
        .shell()
        .sidecar(SIDECAR_NAME)
        .map_err(|error| error.to_string())?
        .args(["--data-dir", data_dir_arg.as_str()])
        .env("COPYRIGHT_AGENT_SESSION_TOKEN", &token)
        .env("COPYRIGHT_AGENT_PARENT_PID", std::process::id().to_string());
    let (mut events, child) = command.spawn().map_err(|error| error.to_string())?;
    // The frozen Python sidecar extracts and imports document/rendering libraries
    // on a cold start. Twelve seconds was too aggressive on a busy workstation and
    // left an orphaned process behind after the UI had already reported a failure.
    let handshake_result = tokio::time::timeout(Duration::from_secs(45), async {
        let mut stderr = Vec::new();
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    return (
                        Some(String::from_utf8_lossy(&bytes).trim().to_owned()),
                        stderr,
                    );
                }
                CommandEvent::Error(message) => stderr.push(message),
                CommandEvent::Stderr(bytes) => {
                    let message = String::from_utf8_lossy(&bytes).trim().to_owned();
                    if !message.is_empty() {
                        stderr.push(message);
                    }
                }
                _ => {}
            }
        }
        (None, stderr)
    })
    .await;
    let (line, stderr) = match handshake_result {
        Ok((Some(line), stderr)) => (line, stderr),
        Ok((None, stderr)) => {
            let _ = child.kill();
            let detail = stderr
                .last()
                .cloned()
                .unwrap_or_else(|| "no diagnostic output".into());
            return Err(format!("sidecar exited before handshake: {detail}"));
        }
        Err(_) => {
            let _ = child.kill();
            return Err(
                "sidecar cold start exceeded 45 seconds; process was stopped, please retry".into(),
            );
        }
    };
    let handshake: SidecarHandshake = serde_json::from_str(&line).map_err(|_| {
        let detail = stderr.last().cloned().unwrap_or_else(|| line.clone());
        format!("invalid sidecar handshake: {detail}")
    })?;
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
    if document.quality.status != "passed" {
        return Err(if document.quality.status == "outdated" {
            "source document was produced under an obsolete generator or QA policy; regenerate and recheck before export".into()
        } else {
            "source document has not passed the current quality check".into()
        });
    }
    let data_dir = runtime_data_dir(&app)?;
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
    let destination = validate_docx_destination(&destination)?;
    fs::copy(source, destination).map_err(|_| "failed to export source document")?;
    Ok(())
}

#[tauri::command]
async fn export_manual_document(
    job_id: String,
    version: u32,
    destination: String,
    review_draft: bool,
    state: State<'_, SidecarState>,
) -> Result<ManualExportResult, String> {
    validate_task_id(&job_id)?;
    if version == 0 {
        return Err("invalid manual document version".into());
    }
    let destination = validate_docx_destination(&destination)?;
    let connection = active_sidecar(&state)?;
    let client = reqwest::Client::new();
    let response = client
        .get(format!(
            "{}/api/v1/manual-jobs/{}/documents/{}/download?review={}",
            connection.base_url, job_id, version, review_draft
        ))
        .header("X-Session-Token", &connection.session_token)
        .timeout(Duration::from_secs(30))
        .send()
        .await
        .map_err(|_| "manual document download failed")?;
    if !response.status().is_success() {
        return Err("manual document is unavailable or failed integrity verification".into());
    }
    let sha256 = response
        .headers()
        .get("X-Artifact-SHA256")
        .and_then(|value| value.to_str().ok())
        .filter(|value| value.len() == 64 && value.chars().all(|item| item.is_ascii_hexdigit()))
        .ok_or("manual document checksum is unavailable")?
        .to_ascii_lowercase();
    let body = response
        .bytes()
        .await
        .map_err(|_| "manual document body is invalid")?;
    let size_bytes = write_verified_export(&destination, &body)?;
    let destination_path = destination.to_string_lossy().into_owned();
    let receipt = ManualExportReceiptRequest {
        document_version: version,
        export_kind: if review_draft { "review" } else { "formal" }.into(),
        destination_path: destination_path.clone(),
        size_bytes,
        sha256: sha256.clone(),
    };
    let receipt_recorded = client
        .post(format!(
            "{}/api/v1/manual-jobs/{}/exports",
            connection.base_url, job_id
        ))
        .header("X-Session-Token", &connection.session_token)
        .json(&receipt)
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .map(|response| response.status().is_success())
        .unwrap_or(false);
    Ok(ManualExportResult {
        destination_path,
        size_bytes,
        sha256,
        verified: true,
        receipt_recorded,
    })
}

fn write_verified_export(destination: &Path, body: &[u8]) -> Result<u64, String> {
    let parent = destination.parent().ok_or("invalid export destination")?;
    if !parent.is_dir() {
        return Err("export destination directory is unavailable".into());
    }
    let name = destination
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or("invalid export filename")?;
    let temporary = parent.join(format!(".{name}.{}.tmp", &random_token()[..12]));
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|_| "failed to create temporary export file")?;
    if let Err(error) = file.write_all(body).and_then(|_| file.sync_all()) {
        let _ = fs::remove_file(&temporary);
        return Err(format!("failed to write temporary export file: {error}"));
    }
    drop(file);

    let backup = parent.join(format!(".{name}.{}.bak", &random_token()[..12]));
    let had_existing = destination.exists();
    if had_existing {
        fs::rename(destination, &backup)
            .map_err(|_| "failed to prepare existing export for replacement")?;
    }
    if let Err(error) = fs::rename(&temporary, destination) {
        if had_existing {
            let _ = fs::rename(&backup, destination);
        }
        let _ = fs::remove_file(&temporary);
        return Err(format!(
            "failed to finalize manual document export: {error}"
        ));
    }
    let written = match fs::read(destination) {
        Ok(value) if value.as_slice() == body => value,
        Ok(_) => {
            let _ = fs::remove_file(destination);
            if had_existing {
                let _ = fs::rename(&backup, destination);
            }
            return Err("exported document failed byte-for-byte verification".into());
        }
        Err(_) => {
            let _ = fs::remove_file(destination);
            if had_existing {
                let _ = fs::rename(&backup, destination);
            }
            return Err("failed to verify exported document".into());
        }
    };
    if had_existing {
        let _ = fs::remove_file(&backup);
    }
    Ok(written.len() as u64)
}

#[tauri::command]
async fn export_manual_figure_asset(
    job_id: String,
    figure_key: String,
    asset_format: String,
    destination: String,
    state: State<'_, SidecarState>,
) -> Result<(), String> {
    validate_task_id(&job_id)?;
    validate_asset_key(&figure_key)?;
    let extension = match asset_format.as_str() {
        "drawio" => "drawio",
        "svg" => "svg",
        "png" => "png",
        _ => return Err("unsupported diagram asset format".into()),
    };
    let destination = validate_asset_destination(&destination, extension)?;
    let connection = active_sidecar(&state)?;
    let response = reqwest::Client::new()
        .get(format!(
            "{}/api/v1/manual-jobs/{}/figures/{}.{}",
            connection.base_url, job_id, figure_key, extension
        ))
        .header("X-Session-Token", connection.session_token)
        .timeout(Duration::from_secs(30))
        .send()
        .await
        .map_err(|_| "diagram asset download failed")?;
    if !response.status().is_success() {
        return Err("diagram asset is unavailable".into());
    }
    let body = response
        .bytes()
        .await
        .map_err(|_| "diagram asset body is invalid")?;
    fs::write(&destination, body).map_err(|_| "failed to export diagram asset")?;
    Ok(())
}

#[tauri::command]
fn reveal_exported_document(path: String) -> Result<(), String> {
    let path = PathBuf::from(path);
    if !path.is_absolute()
        || !path.is_file()
        || path
            .extension()
            .and_then(|value| value.to_str())
            .map(|value| value.eq_ignore_ascii_case("docx"))
            != Some(true)
    {
        return Err("exported document is unavailable".into());
    }
    reveal_in_file_manager(&path)
}

#[tauri::command]
fn reveal_exported_asset(path: String) -> Result<(), String> {
    let path = PathBuf::from(path);
    let allowed = ["docx", "drawio", "svg", "png"];
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| value.to_ascii_lowercase());
    if !path.is_absolute()
        || !path.is_file()
        || !extension
            .as_ref()
            .is_some_and(|value| allowed.contains(&value.as_str()))
    {
        return Err("exported asset is unavailable".into());
    }
    reveal_in_file_manager(&path)
}

fn active_sidecar(state: &State<'_, SidecarState>) -> Result<SidecarConnection, String> {
    state
        .session
        .lock()
        .map_err(|_| "sidecar state poisoned")?
        .as_ref()
        .map(|session| session.connection.clone())
        .ok_or("本地服务尚未连接".into())
}

async fn read_model_credential(
    state: &State<'_, SidecarState>,
    config_id: &str,
) -> Result<String, String> {
    validate_config_id(config_id)?;
    let connection = active_sidecar(state)?;
    let response = reqwest::Client::new()
        .get(format!(
            "{}/api/v1/model-credentials/{config_id}",
            connection.base_url
        ))
        .header("X-Session-Token", connection.session_token)
        .send()
        .await
        .map_err(|_| "无法读取加密凭据库")?;
    if !response.status().is_success() {
        return Err("未找到该模型的 API Key".into());
    }
    response
        .json::<serde_json::Value>()
        .await
        .ok()
        .and_then(|value| {
            value
                .get("api_key")
                .and_then(|key| key.as_str())
                .map(str::to_owned)
        })
        .filter(|value| !value.is_empty())
        .ok_or("加密凭据解密失败".into())
}

#[tauri::command]
async fn store_model_credential(
    state: State<'_, SidecarState>,
    config_id: String,
    api_key: String,
) -> Result<(), String> {
    validate_config_id(&config_id)?;
    if api_key.trim().len() < 8 {
        return Err("API Key 长度不足".into());
    }
    let connection = active_sidecar(&state)?;
    let response = reqwest::Client::new()
        .put(format!(
            "{}/api/v1/model-credentials/{config_id}",
            connection.base_url
        ))
        .header("X-Session-Token", connection.session_token)
        .json(&serde_json::json!({"api_key": api_key.trim()}))
        .send()
        .await
        .map_err(|_| "API Key 写入加密凭据库失败")?;
    if !response.status().is_success() {
        return Err("API Key 加密保存失败".into());
    }
    if read_model_credential(&state, &config_id).await? == api_key.trim() {
        Ok(())
    } else {
        Err("API Key 保存后回读校验失败".into())
    }
}

#[tauri::command]
async fn delete_model_credential(
    state: State<'_, SidecarState>,
    config_id: String,
) -> Result<(), String> {
    validate_config_id(&config_id)?;
    let connection = active_sidecar(&state)?;
    let response = reqwest::Client::new()
        .delete(format!(
            "{}/api/v1/model-credentials/{config_id}",
            connection.base_url
        ))
        .header("X-Session-Token", connection.session_token)
        .send()
        .await
        .map_err(|_| "加密凭据删除失败")?;
    if response.status().is_success() {
        Ok(())
    } else {
        Err("加密凭据删除失败".into())
    }
}

#[tauri::command]
async fn has_model_credential(
    state: State<'_, SidecarState>,
    config_id: String,
) -> Result<bool, String> {
    validate_config_id(&config_id)?;
    let connection = active_sidecar(&state)?;
    let response = reqwest::Client::new()
        .get(format!(
            "{}/api/v1/model-credentials/{config_id}/status",
            connection.base_url
        ))
        .header("X-Session-Token", connection.session_token)
        .send()
        .await
        .map_err(|_| "无法检查加密凭据状态")?;
    if !response.status().is_success() {
        return Err("无法检查加密凭据状态".into());
    }
    Ok(response
        .json::<serde_json::Value>()
        .await
        .ok()
        .and_then(|value| value.get("available").and_then(|item| item.as_bool()))
        .unwrap_or(false))
}

#[tauri::command]
async fn probe_model_config(
    state: State<'_, SidecarState>,
    request: ModelProbeRequest,
) -> Result<ModelProbeResult, String> {
    validate_config_id(&request.config_id)?;
    let base = normalize_model_base_url(&request.protocol_id, &request.base_url)?;
    if !base.starts_with("https://")
        && !base.starts_with("http://127.0.0.1")
        && !base.starts_with("http://localhost")
    {
        return Err("远程模型地址必须使用 HTTPS；本机 Ollama 可使用 HTTP".into());
    }
    let (url, credential_required) = match request.protocol_id.as_str() {
        "openai_compatible" => (format!("{base}/models"), true),
        "anthropic" => (format!("{base}/models"), true),
        "ollama" => (format!("{base}/api/tags"), false),
        _ => return Err("不支持的模型协议".into()),
    };
    let credential = if credential_required {
        Some(read_model_credential(&state, &request.config_id).await?)
    } else {
        None
    };
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|_| "无法创建模型连接")?;
    let mut call = client.get(&url);
    if let Some(key) = credential.as_ref() {
        call = if request.protocol_id == "anthropic" {
            call.header("x-api-key", key)
                .header("anthropic-version", "2023-06-01")
        } else {
            call.bearer_auth(key)
        };
    }
    let response = call
        .send()
        .await
        .map_err(|error| format!("模型服务连接失败：{error}"))?;
    if !response.status().is_success() {
        let status = response.status().as_u16();
        if request.protocol_id == "openai_compatible"
            && is_senseaudio(&base)
            && matches!(status, 404 | 405)
        {
            let discovered_models = SENSEAUDIO_MODELS
                .iter()
                .map(|value| (*value).to_owned())
                .collect();
            return validate_catalog_model(request, base, credential, client, discovered_models)
                .await;
        }
        return Err(format!(
            "模型列表请求失败（HTTP {status}，请求地址：{url}）"
        ));
    }
    let payload: serde_json::Value = response
        .json()
        .await
        .map_err(|_| "模型服务返回了无法识别的数据")?;
    let items = if request.protocol_id == "ollama" {
        payload.get("models").and_then(|value| value.as_array())
    } else {
        payload.get("data").and_then(|value| value.as_array())
    };
    let discovered_models: Vec<String> = items
        .into_iter()
        .flatten()
        .filter_map(|item| {
            item.get(if request.protocol_id == "ollama" {
                "name"
            } else {
                "id"
            })
            .and_then(|value| value.as_str())
            .map(str::to_owned)
        })
        .take(100)
        .collect();
    let model_found = request.model_name.is_empty()
        || discovered_models
            .iter()
            .any(|value| value == &request.model_name);
    Ok(ModelProbeResult {
        available: true,
        model_found,
        discovered_models,
        normalized_base_url: base,
        discovery_source: "token_api".into(),
        warning: None,
    })
}

#[tauri::command]
async fn test_model_connection(
    state: State<'_, SidecarState>,
    request: ModelProbeRequest,
) -> Result<ModelConnectionTestResult, String> {
    validate_config_id(&request.config_id)?;
    if request.model_name.trim().is_empty() {
        return Err("请选择要测试的模型".into());
    }
    let base = normalize_model_base_url(&request.protocol_id, &request.base_url)?;
    if !base.starts_with("https://")
        && !base.starts_with("http://127.0.0.1")
        && !base.starts_with("http://localhost")
    {
        return Err("远程模型地址必须使用 HTTPS；本机服务可使用 HTTP".into());
    }
    let credential = if request.protocol_id == "ollama" {
        None
    } else {
        Some(
            read_model_credential(&state, &request.config_id)
                .await
                .map_err(|_| "该连接的 API Key 不存在，请编辑连接后重新填写")?,
        )
    };
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(45))
        .build()
        .map_err(|_| "无法创建模型连接")?;
    let started = std::time::Instant::now();
    if request.protocol_id == "openai_compatible" && is_senseaudio(&base) {
        let key = credential.as_ref().unwrap();
        let candidates = [
            (
                "messages",
                format!("{base}/messages"),
                serde_json::json!({
                    "model": request.model_name, "messages": [{"role": "user", "content": "仅回复 OK"}], "max_tokens": 2
                }),
            ),
            (
                "chat_completions",
                format!("{base}/chat/completions"),
                serde_json::json!({
                    "model": request.model_name, "messages": [{"role": "user", "content": "仅回复 OK"}],
                    "max_tokens": 2, "temperature": 0
                }),
            ),
            (
                "responses",
                format!("{base}/responses"),
                serde_json::json!({
                    "model": request.model_name, "input": "仅回复 OK", "max_output_tokens": 2
                }),
            ),
        ];
        let mut failures = Vec::new();
        for (mode, endpoint, payload) in candidates {
            let response = client
                .post(endpoint)
                .bearer_auth(key)
                .json(&payload)
                .send()
                .await
                .map_err(|error| format!("连接失败：{error}"))?;
            if response.status().is_success() {
                return Ok(ModelConnectionTestResult {
                    ok: true,
                    elapsed_ms: started.elapsed().as_millis(),
                    endpoint_mode: mode.into(),
                });
            }
            let status = response.status().as_u16();
            let detail: String = response
                .text()
                .await
                .unwrap_or_default()
                .chars()
                .take(160)
                .collect();
            failures.push(format!("{mode}: HTTP {status} {detail}"));
        }
        return Err(format!(
            "该模型的三种接口均测试失败：{}",
            failures.join("；")
        ));
    }
    let (response, endpoint_mode) = match request.protocol_id.as_str() {
        "openai_compatible" => client
            .post(format!("{base}/chat/completions"))
            .bearer_auth(credential.as_ref().unwrap())
            .json(&serde_json::json!({
                "model": request.model_name, "messages": [{"role": "user", "content": "仅回复 OK"}],
                "max_tokens": 2, "temperature": 0
            }))
            .send()
            .await
            .map(|response| (response, "chat_completions")),
        "anthropic" => client
            .post(format!("{base}/messages"))
            .header("x-api-key", credential.as_ref().unwrap())
            .header("anthropic-version", "2023-06-01")
            .json(&serde_json::json!({
                "model": request.model_name, "messages": [{"role": "user", "content": "仅回复 OK"}],
                "max_tokens": 2, "temperature": 0
            }))
            .send()
            .await
            .map(|response| (response, "messages")),
        "ollama" => client
            .post(format!("{base}/api/chat"))
            .json(&serde_json::json!({
                "model": request.model_name, "messages": [{"role": "user", "content": "仅回复 OK"}],
                "stream": false, "options": {"num_predict": 2, "temperature": 0}
            }))
            .send()
            .await
            .map(|response| (response, "ollama_chat")),
        _ => return Err("不支持的模型协议".into()),
    }
    .map_err(|error| format!("连接失败：{error}"))?;
    let elapsed_ms = started.elapsed().as_millis();
    if !response.status().is_success() {
        let status = response.status().as_u16();
        let detail = response.text().await.unwrap_or_default();
        let concise: String = detail.chars().take(240).collect();
        return Err(if concise.is_empty() {
            format!("测试失败（HTTP {status}）")
        } else {
            format!("测试失败（HTTP {status}）：{concise}")
        });
    }
    Ok(ModelConnectionTestResult {
        ok: true,
        elapsed_ms,
        endpoint_mode: endpoint_mode.into(),
    })
}

#[tauri::command]
async fn launch_capture_project(
    job_id: String,
    candidate_id: String,
    target_url: String,
    app: tauri::AppHandle,
    sidecar: State<'_, SidecarState>,
    capture: State<'_, ProjectCaptureState>,
) -> Result<ProjectCaptureStatus, String> {
    validate_resource_id(&job_id, "job")?;
    validate_resource_id(&candidate_id, "candidate")?;
    validate_loopback_url(&target_url)?;
    let connection = sidecar_connection(&sidecar)?;
    let response = reqwest::Client::new()
        .get(format!(
            "{}/api/v1/manual-jobs/{}/screenshots/launch-plan/{}",
            connection.base_url, job_id, candidate_id,
        ))
        .header("X-Session-Token", &connection.session_token)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .map_err(|_| "无法验证项目启动候选")?;
    if !response.status().is_success() {
        return Err("项目启动候选已失效，请刷新截图页面后重试".into());
    }
    let candidate: CaptureLaunchCandidate =
        response.json().await.map_err(|_| "项目启动候选格式无效")?;
    if candidate.id != candidate_id || !capture_program_allowed(&candidate.program) {
        return Err("项目启动候选未通过安全校验".into());
    }
    validate_loopback_url(&candidate.default_url)?;
    let cwd = PathBuf::from(&candidate.working_directory);
    if !cwd.is_absolute() || !cwd.is_dir() {
        return Err("项目启动目录不可用".into());
    }
    {
        let mut sessions = capture
            .sessions
            .lock()
            .map_err(|_| "capture state poisoned")?;
        if let Some(existing) = sessions.get_mut(&job_id) {
            if existing.children.iter_mut().any(|child| {
                child
                    .try_wait()
                    .map(|value| value.is_none())
                    .unwrap_or(false)
            }) {
                return Err("当前说明书已有运行中的项目启动会话".into());
            }
            sessions.remove(&job_id);
        }
    }
    let log_dir = runtime_data_dir(&app)?
        .join("capture-sessions")
        .join(&job_id);
    fs::create_dir_all(&log_dir).map_err(|_| "无法创建启动日志目录")?;
    let log_path = log_dir.join("project.log");
    fs::write(&log_path, b"").map_err(|_| "无法创建项目启动日志")?;
    let services = if candidate.services.is_empty() {
        vec![CaptureLaunchService {
            program: candidate.program.clone(),
            args: candidate.args.clone(),
            working_directory: candidate.working_directory.clone(),
            command_preview: candidate.command_preview.clone(),
        }]
    } else {
        candidate.services.clone()
    };
    let mut children = Vec::new();
    for service in services {
        if !capture_program_allowed(&service.program) {
            for child in &mut children {
                stop_process_tree(child);
            }
            return Err("项目启动候选包含未获准的程序".into());
        }
        let service_cwd = PathBuf::from(&service.working_directory);
        if !service_cwd.is_absolute() || !service_cwd.is_dir() {
            for child in &mut children {
                stop_process_tree(child);
            }
            return Err("项目启动目录不可用".into());
        }
        let stdout = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .map_err(|_| "无法创建项目启动日志")?;
        let stderr = stdout.try_clone().map_err(|_| "无法准备项目错误日志")?;
        let mut command = Command::new(&service.program);
        command
            .args(&service.args)
            .current_dir(&service_cwd)
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr))
            .stdin(Stdio::null());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        match command.spawn() {
            Ok(child) => children.push(child),
            Err(error) => {
                for child in &mut children {
                    stop_process_tree(child);
                }
                return Err(format!(
                    "无法启动 {}：{}。请确认运行环境已经可用",
                    service.command_preview, error
                ));
            }
        }
    }
    let pid = children
        .first()
        .map(Child::id)
        .ok_or("启动方案没有可执行服务")?;
    capture
        .sessions
        .lock()
        .map_err(|_| "capture state poisoned")?
        .insert(
            job_id.clone(),
            ProjectCaptureSession {
                children,
                started_at: Instant::now(),
                target_url: target_url.clone(),
                command_preview: candidate.command_preview.clone(),
                log_path: log_path.clone(),
            },
        );
    Ok(ProjectCaptureStatus {
        job_id,
        status: "starting".into(),
        pid,
        target_url,
        command_preview: candidate.command_preview,
        elapsed_seconds: 0,
        log_tail: read_log_tail(&log_path),
        exit_code: None,
    })
}

#[tauri::command]
fn capture_project_status(
    job_id: String,
    capture: State<'_, ProjectCaptureState>,
) -> Result<ProjectCaptureStatus, String> {
    validate_resource_id(&job_id, "job")?;
    let mut sessions = capture
        .sessions
        .lock()
        .map_err(|_| "capture state poisoned")?;
    let session = sessions
        .get_mut(&job_id)
        .ok_or("当前说明书没有项目启动会话")?;
    let pid = session
        .children
        .first()
        .map(Child::id)
        .ok_or("项目启动会话没有进程")?;
    let mut exits = Vec::new();
    for child in &mut session.children {
        exits.push(child.try_wait().map_err(|_| "无法读取项目进程状态")?);
    }
    let exited = exits.iter().filter(|value| value.is_some()).count();
    let status = if exited == 0 {
        "running"
    } else if exited == exits.len() {
        "exited"
    } else {
        "partial_failure"
    };
    Ok(ProjectCaptureStatus {
        job_id,
        status: status.into(),
        pid,
        target_url: session.target_url.clone(),
        command_preview: session.command_preview.clone(),
        elapsed_seconds: session.started_at.elapsed().as_secs(),
        log_tail: read_log_tail(&session.log_path),
        exit_code: exits
            .into_iter()
            .flatten()
            .next()
            .and_then(|value| value.code()),
    })
}

#[tauri::command]
fn stop_capture_project(
    job_id: String,
    capture: State<'_, ProjectCaptureState>,
) -> Result<ProjectCaptureStatus, String> {
    validate_resource_id(&job_id, "job")?;
    let mut session = capture
        .sessions
        .lock()
        .map_err(|_| "capture state poisoned")?
        .remove(&job_id)
        .ok_or("当前说明书没有项目启动会话")?;
    let pid = session.children.first().map(Child::id).unwrap_or(0);
    for child in &mut session.children {
        stop_process_tree(child);
    }
    Ok(ProjectCaptureStatus {
        job_id,
        status: "stopped".into(),
        pid,
        target_url: session.target_url,
        command_preview: session.command_preview,
        elapsed_seconds: session.started_at.elapsed().as_secs(),
        log_tail: read_log_tail(&session.log_path),
        exit_code: None,
    })
}

#[tauri::command]
async fn capture_project_page(
    job_id: String,
    target_url: String,
    app: tauri::AppHandle,
    capture: State<'_, ProjectCaptureState>,
) -> Result<ProjectPageCaptureResult, String> {
    validate_resource_id(&job_id, "job")?;
    let requested = validate_loopback_url(&target_url)?;
    {
        let mut sessions = capture
            .sessions
            .lock()
            .map_err(|_| "capture state poisoned")?;
        let session = sessions.get_mut(&job_id).ok_or("请先启动并授权当前项目")?;
        if session.children.iter_mut().any(|child| {
            child
                .try_wait()
                .map(|value| value.is_some())
                .unwrap_or(true)
        }) {
            return Err("项目至少有一个服务已经退出，请查看启动日志并重新启动完整服务组".into());
        }
        let authorized = validate_loopback_url(&session.target_url)?;
        if requested.host_str() != authorized.host_str()
            || requested.port_or_known_default() != authorized.port_or_known_default()
        {
            return Err("截图地址必须与已授权启动会话使用相同的本机主机和端口".into());
        }
    }
    let ready = reqwest::Client::builder()
        .timeout(Duration::from_secs(4))
        .build()
        .map_err(|_| "无法创建本机页面探测连接")?
        .get(requested.clone())
        .send()
        .await
        .map_err(|_| "本机页面尚未就绪；请查看启动日志，确认服务地址和端口后重试")?;
    if !ready.status().is_success() {
        return Err(format!(
            "本机页面返回 HTTP {}，暂不采集错误页面",
            ready.status().as_u16()
        ));
    }
    let data_dir = runtime_data_dir(&app)?;
    let output_dir = data_dir
        .join("capture-sessions")
        .join(&job_id)
        .join("captures");
    fs::create_dir_all(&output_dir).map_err(|_| "无法创建截图临时目录")?;
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "系统时间无效")?
        .as_millis();
    let output = output_dir.join(format!("page-{timestamp}.png"));
    let url = target_url.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        render_page_with_system_browser(&url, &output)
    })
    .await
    .map_err(|_| "浏览器截图任务异常终止")??;
    Ok(result)
}

fn sidecar_connection(state: &State<'_, SidecarState>) -> Result<SidecarConnection, String> {
    state
        .session
        .lock()
        .map_err(|_| "sidecar state poisoned")?
        .as_ref()
        .map(|session| session.connection.clone())
        .ok_or("local service is not connected".into())
}

fn validate_resource_id(value: &str, label: &str) -> Result<(), String> {
    if value.len() < 8
        || value.len() > 80
        || !value.chars().all(|character| {
            character.is_ascii_alphanumeric() || character == '-' || character == '_'
        })
    {
        return Err(format!("invalid {label} id"));
    }
    Ok(())
}

fn validate_loopback_url(value: &str) -> Result<reqwest::Url, String> {
    let url = reqwest::Url::parse(value).map_err(|_| "截图地址格式无效")?;
    if url.scheme() != "http" || !matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1"))
    {
        return Err("截图适配器只允许访问本机 HTTP 地址".into());
    }
    if url.username() != "" || url.password().is_some() {
        return Err("截图地址不能包含用户名或密码".into());
    }
    Ok(url)
}

fn capture_program_allowed(program: &str) -> bool {
    matches!(program, "npm" | "pnpm" | "yarn" | "bun" | "mvn")
        || Path::new(program)
            .file_name()
            .and_then(|value| value.to_str())
            .map(|value| matches!(value, "mvnw" | "mvnw.cmd"))
            .unwrap_or(false)
}

fn read_log_tail(path: &Path) -> String {
    let Ok(mut file) = fs::File::open(path) else {
        return String::new();
    };
    let Ok(length) = file.metadata().map(|value| value.len()) else {
        return String::new();
    };
    let start = length.saturating_sub(12 * 1024);
    if file.seek(SeekFrom::Start(start)).is_err() {
        return String::new();
    }
    let mut buffer = Vec::new();
    if file.read_to_end(&mut buffer).is_err() {
        return String::new();
    }
    String::from_utf8_lossy(&buffer)
        .chars()
        .rev()
        .take(6000)
        .collect::<String>()
        .chars()
        .rev()
        .collect()
}

fn stop_process_tree(child: &mut Child) {
    let pid = child.id();
    #[cfg(unix)]
    {
        let _ = Command::new("kill")
            .args(["-TERM", &format!("-{pid}")])
            .status();
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn render_page_with_system_browser(
    url: &str,
    output: &Path,
) -> Result<ProjectPageCaptureResult, String> {
    let browser = find_system_browser()
        .ok_or("未发现可用于截图的 Chrome、Edge、Chromium 或 Brave；仍可使用人工导入截图")?;
    let profile = output
        .parent()
        .ok_or("截图目录无效")?
        .join("browser-profile");
    fs::create_dir_all(&profile).map_err(|_| "无法创建隔离浏览器配置目录")?;
    let mut command = Command::new(&browser);
    command
        .args([
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1440,1000",
            "--virtual-time-budget=5000",
            &format!("--user-data-dir={}", profile.display()),
            &format!("--screenshot={}", output.display()),
            url,
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动系统浏览器截图：{error}"))?;
    let started = Instant::now();
    loop {
        if output.is_file() && output.metadata().map(|value| value.len()).unwrap_or(0) > 0 {
            // Chrome can keep an updater helper alive after the one-shot screenshot has
            // already been flushed. The image is the terminal condition; stop the isolated
            // process group instead of making the user wait for unrelated updater cleanup.
            std::thread::sleep(Duration::from_millis(150));
            stop_process_tree(&mut child);
            break;
        }
        if let Some(status) = child.try_wait().map_err(|_| "无法读取浏览器截图状态")? {
            if !status.success()
                || !output.is_file()
                || output.metadata().map(|v| v.len()).unwrap_or(0) == 0
            {
                let mut detail = String::new();
                if let Some(mut stderr) = child.stderr.take() {
                    let _ = stderr.read_to_string(&mut detail);
                }
                return Err(format!(
                    "浏览器未能生成截图：{}",
                    detail.chars().take(300).collect::<String>()
                ));
            }
            break;
        }
        if started.elapsed() > Duration::from_secs(30) {
            let _ = child.kill();
            return Err("浏览器截图超过 30 秒，已停止".into());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Ok(ProjectPageCaptureResult {
        path: output.to_string_lossy().into_owned(),
        url: url.into(),
        width: 1440,
        height: 1000,
        browser: browser.to_string_lossy().into_owned(),
    })
}

fn find_system_browser() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    #[cfg(target_os = "macos")]
    candidates.extend(
        [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
        .into_iter()
        .map(PathBuf::from),
    );
    #[cfg(target_os = "windows")]
    {
        for root in [
            std::env::var_os("PROGRAMFILES"),
            std::env::var_os("PROGRAMFILES(X86)"),
            std::env::var_os("LOCALAPPDATA"),
        ]
        .into_iter()
        .flatten()
        {
            for suffix in [
                "Google/Chrome/Application/chrome.exe",
                "Microsoft/Edge/Application/msedge.exe",
                "BraveSoftware/Brave-Browser/Application/brave.exe",
            ] {
                candidates.push(PathBuf::from(&root).join(suffix));
            }
        }
    }
    if let Some(path) = candidates.into_iter().find(|path| path.is_file()) {
        return Some(path);
    }
    for program in [
        "google-chrome",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "brave-browser",
    ] {
        if Command::new(program)
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
        {
            return Some(PathBuf::from(program));
        }
    }
    None
}

fn normalize_model_base_url(protocol: &str, raw: &str) -> Result<String, String> {
    let mut base = raw.trim().trim_end_matches('/').to_owned();
    let suffixes: &[&str] = match protocol {
        "openai_compatible" => &["/chat/completions", "/responses"],
        "anthropic" => &["/messages"],
        _ => &[],
    };
    for suffix in suffixes {
        if base.ends_with(suffix) {
            base.truncate(base.len() - suffix.len());
            break;
        }
    }
    if base.is_empty() {
        return Err("Base URL 不能为空".into());
    }
    Ok(base.trim_end_matches('/').to_owned())
}

fn is_senseaudio(base: &str) -> bool {
    reqwest::Url::parse(base)
        .ok()
        .and_then(|url| url.host_str().map(str::to_owned))
        .map(|host| host == "api.senseaudio.cn")
        .unwrap_or(false)
}

async fn validate_catalog_model(
    request: ModelProbeRequest,
    base: String,
    credential: Option<String>,
    client: reqwest::Client,
    discovered_models: Vec<String>,
) -> Result<ModelProbeResult, String> {
    let warning = "该服务未提供模型列表接口；模型来自 SenseAudio 官方目录，保存时会发送极小请求验证当前 Token 权限。";
    if request.model_name.is_empty() {
        return Ok(ModelProbeResult {
            available: true,
            model_found: true,
            discovered_models,
            normalized_base_url: base,
            discovery_source: "provider_catalog".into(),
            warning: Some(warning.into()),
        });
    }
    if !discovered_models
        .iter()
        .any(|value| value == &request.model_name)
    {
        return Err("所选模型不在 SenseAudio 当前官方模型目录中".into());
    }
    let key = credential.ok_or("未找到该模型的 API Key")?;
    let endpoint = if is_senseaudio(&base) {
        format!("{base}/messages")
    } else {
        format!("{base}/chat/completions")
    };
    let response = client
        .post(&endpoint)
        .bearer_auth(key)
        .json(&serde_json::json!({
            "model": request.model_name,
            "messages": [{"role": "user", "content": "仅回复 OK"}],
            "max_tokens": 1,
            "temperature": 0
        }))
        .send()
        .await
        .map_err(|error| format!("模型权限验证连接失败：{error}"))?;
    if !response.status().is_success() {
        return Err(format!(
            "所选模型权限验证失败（HTTP {}，请求地址：{endpoint}）",
            response.status().as_u16()
        ));
    }
    Ok(ModelProbeResult {
        available: true,
        model_found: true,
        discovered_models,
        normalized_base_url: base,
        discovery_source: "provider_catalog_verified".into(),
        warning: Some(warning.into()),
    })
}

fn validate_config_id(value: &str) -> Result<(), String> {
    if value.len() < 8
        || value.len() > 64
        || !value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '-')
    {
        return Err("invalid model config id".into());
    }
    Ok(())
}

async fn resolve_source_document(
    task_id: &str,
    app: &tauri::AppHandle,
    state: &State<'_, SidecarState>,
) -> Result<PathBuf, String> {
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
    let data_dir = runtime_data_dir(app)?;
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

fn validate_asset_key(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 80
        || !value.chars().all(|character| {
            character.is_ascii_alphanumeric() || character == '-' || character == '_'
        })
    {
        return Err("invalid diagram asset key".into());
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
    let (program, arguments) = file_manager_command(std::env::consts::OS, path)?;
    let status = Command::new(program).args(arguments).status();
    status
        .map_err(|_| "failed to start file manager")?
        .success()
        .then_some(())
        .ok_or_else(|| "file manager could not reveal source document".into())
}

fn validate_docx_destination(value: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(value);
    if !path.is_absolute()
        || path
            .extension()
            .and_then(|extension| extension.to_str())
            .map(|extension| extension.eq_ignore_ascii_case("docx"))
            != Some(true)
    {
        return Err("export destination must be an absolute .docx path".into());
    }
    Ok(path)
}

fn validate_asset_destination(value: &str, extension: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(value);
    if !path.is_absolute()
        || path
            .extension()
            .and_then(|value| value.to_str())
            .map(|value| value.eq_ignore_ascii_case(extension))
            != Some(true)
    {
        return Err(format!(
            "export destination must be an absolute .{extension} path"
        ));
    }
    Ok(path)
}

fn file_manager_command(platform: &str, path: &Path) -> Result<(String, Vec<OsString>), String> {
    match platform {
        "macos" => Ok((
            "open".into(),
            vec![OsString::from("-R"), path.as_os_str().into()],
        )),
        "windows" => Ok((
            "explorer".into(),
            vec![OsString::from(format!("/select,{}", path.display()))],
        )),
        _ => Ok((
            "xdg-open".into(),
            vec![path
                .parent()
                .ok_or("invalid artifact parent")?
                .as_os_str()
                .into()],
        )),
    }
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

impl Drop for ProjectCaptureState {
    fn drop(&mut self) {
        if let Ok(sessions) = self.sessions.get_mut() {
            for (_, mut session) in sessions.drain() {
                for child in &mut session.children {
                    stop_process_tree(child);
                }
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if std::env::var_os("COPYRIGHT_AGENT_DATA_DIR").is_some() {
                if let Some(window) = app.get_webview_window("main") {
                    window.set_title("软著材料助手 · 开发版")?;
                }
            }
            Ok(())
        })
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState::default())
        .manage(ProjectCaptureState::default())
        .invoke_handler(tauri::generate_handler![
            start_sidecar,
            reveal_source_document,
            export_source_document,
            export_manual_document,
            export_manual_figure_asset,
            reveal_exported_document,
            reveal_exported_asset,
            store_model_credential,
            delete_model_credential,
            has_model_credential,
            probe_model_config,
            test_model_connection,
            launch_capture_project,
            capture_project_status,
            stop_capture_project,
            capture_project_page
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

    #[test]
    fn openai_chat_endpoint_is_normalized_to_api_root() {
        assert_eq!(
            normalize_model_base_url(
                "openai_compatible",
                "https://api.senseaudio.cn/v1/chat/completions/"
            )
            .unwrap(),
            "https://api.senseaudio.cn/v1"
        );
    }

    #[test]
    fn senseaudio_detection_requires_exact_official_host() {
        assert!(is_senseaudio("https://api.senseaudio.cn/v1"));
        assert!(!is_senseaudio("https://api.senseaudio.cn.example.com/v1"));
    }

    #[test]
    fn export_destination_requires_absolute_docx_path() {
        assert!(validate_docx_destination("result.docx").is_err());
        assert!(validate_docx_destination("/tmp/软著材料.docx").is_ok());
        assert!(validate_docx_destination("/tmp/result.pdf").is_err());
    }

    #[test]
    fn diagram_export_validates_key_and_matching_extension() {
        assert!(validate_asset_key("system_architecture_diagram").is_ok());
        assert!(validate_asset_key("../secret").is_err());
        assert!(validate_asset_destination("/tmp/系统架构.drawio", "drawio").is_ok());
        assert!(validate_asset_destination("/tmp/系统架构.svg", "drawio").is_err());
        assert!(validate_asset_destination("系统架构.png", "png").is_err());
    }

    #[test]
    fn file_manager_commands_preserve_unicode_and_spaces_as_one_argument() {
        let path = Path::new("/tmp/导出 文档.docx");
        let (mac_program, mac_args) = file_manager_command("macos", path).unwrap();
        assert_eq!(mac_program, "open");
        assert_eq!(
            mac_args,
            vec![OsString::from("-R"), path.as_os_str().into()]
        );
        let (windows_program, windows_args) = file_manager_command("windows", path).unwrap();
        assert_eq!(windows_program, "explorer");
        assert_eq!(windows_args.len(), 1);
        assert_eq!(
            windows_args[0],
            OsString::from("/select,/tmp/导出 文档.docx")
        );
    }

    #[test]
    fn capture_url_accepts_only_loopback_http_without_credentials() {
        assert!(validate_loopback_url("http://127.0.0.1:5173/dashboard").is_ok());
        assert!(validate_loopback_url("http://localhost:3000").is_ok());
        assert!(validate_loopback_url("https://127.0.0.1:5173").is_err());
        assert!(validate_loopback_url("http://example.com:5173").is_err());
        assert!(validate_loopback_url("http://user:secret@localhost:3000").is_err());
    }

    #[test]
    fn capture_candidate_identifier_rejects_path_syntax() {
        assert!(validate_resource_id("7b3b03f3f9f37f5317ed2b11", "candidate").is_ok());
        assert!(validate_resource_id("../../package.json", "candidate").is_err());
    }

    #[test]
    fn capture_programs_allow_declared_frontend_and_maven_services_only() {
        assert!(capture_program_allowed("npm"));
        assert!(capture_program_allowed("mvn"));
        assert!(capture_program_allowed("/tmp/project/mvnw"));
        assert!(!capture_program_allowed("sh"));
        assert!(!capture_program_allowed("/tmp/project/start-anything"));
    }
}
