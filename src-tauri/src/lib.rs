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

const SENSEAUDIO_MODELS: &[&str] = &[
    "senseaudio-s2", "senseaudio-s2-flash", "senseaudio-s2-lite", "senseaudio-s1",
    "senseaudio-vl-1.0-260319", "senseaudio-vl-lite-1.0-260319",
    "sensenova-6.7-flash-lite", "deepseek-v4-flash-0731", "deepseek-v4-flash",
    "deepseek-v4-pro", "qwen3.6-27b", "qwen3.6-35b-a3b", "kimi-k2.6",
    "glm-5.1", "glm-5.2", "minimax-m2.7", "doubao-seed-2-0-pro-260215",
];

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

fn active_sidecar(state: &State<'_, SidecarState>) -> Result<SidecarConnection, String> {
    state.session.lock().map_err(|_| "sidecar state poisoned")?.as_ref()
        .map(|session| session.connection.clone()).ok_or("本地服务尚未连接".into())
}

async fn read_model_credential(state: &State<'_, SidecarState>, config_id: &str) -> Result<String, String> {
    validate_config_id(config_id)?;
    let connection = active_sidecar(state)?;
    let response = reqwest::Client::new()
        .get(format!("{}/api/v1/model-credentials/{config_id}", connection.base_url))
        .header("X-Session-Token", connection.session_token).send().await
        .map_err(|_| "无法读取加密凭据库")?;
    if !response.status().is_success() { return Err("未找到该模型的 API Key".into()); }
    response.json::<serde_json::Value>().await.ok()
        .and_then(|value| value.get("api_key").and_then(|key| key.as_str()).map(str::to_owned))
        .filter(|value| !value.is_empty()).ok_or("加密凭据解密失败".into())
}

#[tauri::command]
async fn store_model_credential(state: State<'_, SidecarState>, config_id: String, api_key: String) -> Result<(), String> {
    validate_config_id(&config_id)?;
    if api_key.trim().len() < 8 { return Err("API Key 长度不足".into()); }
    let connection = active_sidecar(&state)?;
    let response = reqwest::Client::new()
        .put(format!("{}/api/v1/model-credentials/{config_id}", connection.base_url))
        .header("X-Session-Token", connection.session_token)
        .json(&serde_json::json!({"api_key": api_key.trim()})).send().await
        .map_err(|_| "API Key 写入加密凭据库失败")?;
    if !response.status().is_success() { return Err("API Key 加密保存失败".into()); }
    if read_model_credential(&state, &config_id).await? == api_key.trim() { Ok(()) }
        else { Err("API Key 保存后回读校验失败".into()) }
}

#[tauri::command]
async fn delete_model_credential(state: State<'_, SidecarState>, config_id: String) -> Result<(), String> {
    validate_config_id(&config_id)?;
    let connection = active_sidecar(&state)?;
    let response = reqwest::Client::new()
        .delete(format!("{}/api/v1/model-credentials/{config_id}", connection.base_url))
        .header("X-Session-Token", connection.session_token).send().await
        .map_err(|_| "加密凭据删除失败")?;
    if response.status().is_success() { Ok(()) } else { Err("加密凭据删除失败".into()) }
}

#[tauri::command]
async fn has_model_credential(state: State<'_, SidecarState>, config_id: String) -> Result<bool, String> {
    validate_config_id(&config_id)?;
    let connection = active_sidecar(&state)?;
    let response = reqwest::Client::new()
        .get(format!("{}/api/v1/model-credentials/{config_id}/status", connection.base_url))
        .header("X-Session-Token", connection.session_token).send().await
        .map_err(|_| "无法检查加密凭据状态")?;
    if !response.status().is_success() { return Err("无法检查加密凭据状态".into()); }
    Ok(response.json::<serde_json::Value>().await.ok()
        .and_then(|value| value.get("available").and_then(|item| item.as_bool())).unwrap_or(false))
}

#[tauri::command]
async fn probe_model_config(state: State<'_, SidecarState>, request: ModelProbeRequest) -> Result<ModelProbeResult, String> {
    validate_config_id(&request.config_id)?;
    let base = normalize_model_base_url(&request.protocol_id, &request.base_url)?;
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
        Some(read_model_credential(&state, &request.config_id).await?)
    } else { None };
    let client = reqwest::Client::builder().timeout(Duration::from_secs(15)).build()
        .map_err(|_| "无法创建模型连接")?;
    let mut call = client.get(&url);
    if let Some(key) = credential.as_ref() {
        call = if request.protocol_id == "anthropic" {
            call.header("x-api-key", key).header("anthropic-version", "2023-06-01")
        } else { call.bearer_auth(key) };
    }
    let response = call.send().await.map_err(|error| format!("模型服务连接失败：{error}"))?;
    if !response.status().is_success() {
        let status = response.status().as_u16();
        if request.protocol_id == "openai_compatible" && is_senseaudio(&base)
            && matches!(status, 404 | 405) {
            let discovered_models = SENSEAUDIO_MODELS.iter().map(|value| (*value).to_owned()).collect();
            return validate_catalog_model(request, base, credential, client, discovered_models).await;
        }
        return Err(format!("模型列表请求失败（HTTP {status}，请求地址：{url}）"));
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
    let model_found = request.model_name.is_empty()
        || discovered_models.iter().any(|value| value == &request.model_name);
    Ok(ModelProbeResult { available: true, model_found, discovered_models,
        normalized_base_url: base, discovery_source: "token_api".into(), warning: None })
}

#[tauri::command]
async fn test_model_connection(state: State<'_, SidecarState>, request: ModelProbeRequest) -> Result<ModelConnectionTestResult, String> {
    validate_config_id(&request.config_id)?;
    if request.model_name.trim().is_empty() { return Err("请选择要测试的模型".into()); }
    let base = normalize_model_base_url(&request.protocol_id, &request.base_url)?;
    if !base.starts_with("https://") && !base.starts_with("http://127.0.0.1")
        && !base.starts_with("http://localhost") {
        return Err("远程模型地址必须使用 HTTPS；本机服务可使用 HTTP".into());
    }
    let credential = if request.protocol_id == "ollama" { None } else {
        Some(read_model_credential(&state, &request.config_id).await
            .map_err(|_| "该连接的 API Key 不存在，请编辑连接后重新填写")?)
    };
    let client = reqwest::Client::builder().timeout(Duration::from_secs(45)).build()
        .map_err(|_| "无法创建模型连接")?;
    let started = std::time::Instant::now();
    if request.protocol_id == "openai_compatible" && is_senseaudio(&base) {
        let key = credential.as_ref().unwrap();
        let candidates = [
            ("messages", format!("{base}/messages"), serde_json::json!({
                "model": request.model_name, "messages": [{"role": "user", "content": "仅回复 OK"}], "max_tokens": 2
            })),
            ("chat_completions", format!("{base}/chat/completions"), serde_json::json!({
                "model": request.model_name, "messages": [{"role": "user", "content": "仅回复 OK"}],
                "max_tokens": 2, "temperature": 0
            })),
            ("responses", format!("{base}/responses"), serde_json::json!({
                "model": request.model_name, "input": "仅回复 OK", "max_output_tokens": 2
            })),
        ];
        let mut failures = Vec::new();
        for (mode, endpoint, payload) in candidates {
            let response = client.post(endpoint).bearer_auth(key).json(&payload).send().await
                .map_err(|error| format!("连接失败：{error}"))?;
            if response.status().is_success() {
                return Ok(ModelConnectionTestResult { ok: true,
                    elapsed_ms: started.elapsed().as_millis(), endpoint_mode: mode.into() });
            }
            let status = response.status().as_u16();
            let detail: String = response.text().await.unwrap_or_default().chars().take(160).collect();
            failures.push(format!("{mode}: HTTP {status} {detail}"));
        }
        return Err(format!("该模型的三种接口均测试失败：{}", failures.join("；")));
    }
    let (response, endpoint_mode) = match request.protocol_id.as_str() {
        "openai_compatible" => client.post(format!("{base}/chat/completions"))
            .bearer_auth(credential.as_ref().unwrap()).json(&serde_json::json!({
                "model": request.model_name, "messages": [{"role": "user", "content": "仅回复 OK"}],
                "max_tokens": 2, "temperature": 0
            })).send().await.map(|response| (response, "chat_completions")),
        "anthropic" => client.post(format!("{base}/messages"))
            .header("x-api-key", credential.as_ref().unwrap())
            .header("anthropic-version", "2023-06-01").json(&serde_json::json!({
                "model": request.model_name, "messages": [{"role": "user", "content": "仅回复 OK"}],
                "max_tokens": 2, "temperature": 0
            })).send().await.map(|response| (response, "messages")),
        "ollama" => client.post(format!("{base}/api/chat")).json(&serde_json::json!({
                "model": request.model_name, "messages": [{"role": "user", "content": "仅回复 OK"}],
                "stream": false, "options": {"num_predict": 2, "temperature": 0}
            })).send().await.map(|response| (response, "ollama_chat")),
        _ => return Err("不支持的模型协议".into()),
    }.map_err(|error| format!("连接失败：{error}"))?;
    let elapsed_ms = started.elapsed().as_millis();
    if !response.status().is_success() {
        let status = response.status().as_u16();
        let detail = response.text().await.unwrap_or_default();
        let concise: String = detail.chars().take(240).collect();
        return Err(if concise.is_empty() { format!("测试失败（HTTP {status}）") }
            else { format!("测试失败（HTTP {status}）：{concise}") });
    }
    Ok(ModelConnectionTestResult { ok: true, elapsed_ms, endpoint_mode: endpoint_mode.into() })
}

fn normalize_model_base_url(protocol: &str, raw: &str) -> Result<String, String> {
    let mut base = raw.trim().trim_end_matches('/').to_owned();
    let suffixes: &[&str] = match protocol {
        "openai_compatible" => &["/chat/completions", "/responses"],
        "anthropic" => &["/messages"],
        _ => &[],
    };
    for suffix in suffixes {
        if base.ends_with(suffix) { base.truncate(base.len() - suffix.len()); break; }
    }
    if base.is_empty() { return Err("Base URL 不能为空".into()); }
    Ok(base.trim_end_matches('/').to_owned())
}

fn is_senseaudio(base: &str) -> bool {
    reqwest::Url::parse(base).ok().and_then(|url| url.host_str().map(str::to_owned))
        .map(|host| host == "api.senseaudio.cn").unwrap_or(false)
}

async fn validate_catalog_model(
    request: ModelProbeRequest, base: String, credential: Option<String>,
    client: reqwest::Client, discovered_models: Vec<String>,
) -> Result<ModelProbeResult, String> {
    let warning = "该服务未提供模型列表接口；模型来自 SenseAudio 官方目录，保存时会发送极小请求验证当前 Token 权限。";
    if request.model_name.is_empty() {
        return Ok(ModelProbeResult { available: true, model_found: true, discovered_models,
            normalized_base_url: base, discovery_source: "provider_catalog".into(),
            warning: Some(warning.into()) });
    }
    if !discovered_models.iter().any(|value| value == &request.model_name) {
        return Err("所选模型不在 SenseAudio 当前官方模型目录中".into());
    }
    let key = credential.ok_or("未找到该模型的 API Key")?;
    let endpoint = if is_senseaudio(&base) { format!("{base}/messages") }
        else { format!("{base}/chat/completions") };
    let response = client.post(&endpoint).bearer_auth(key).json(&serde_json::json!({
        "model": request.model_name,
        "messages": [{"role": "user", "content": "仅回复 OK"}],
        "max_tokens": 1,
        "temperature": 0
    })).send().await.map_err(|error| format!("模型权限验证连接失败：{error}"))?;
    if !response.status().is_success() {
        return Err(format!("所选模型权限验证失败（HTTP {}，请求地址：{endpoint}）",
            response.status().as_u16()));
    }
    Ok(ModelProbeResult { available: true, model_found: true, discovered_models,
        normalized_base_url: base, discovery_source: "provider_catalog_verified".into(),
        warning: Some(warning.into()) })
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
            has_model_credential,
            probe_model_config,
            test_model_connection
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
        assert_eq!(normalize_model_base_url("openai_compatible",
            "https://api.senseaudio.cn/v1/chat/completions/").unwrap(),
            "https://api.senseaudio.cn/v1");
    }

    #[test]
    fn senseaudio_detection_requires_exact_official_host() {
        assert!(is_senseaudio("https://api.senseaudio.cn/v1"));
        assert!(!is_senseaudio("https://api.senseaudio.cn.example.com/v1"));
    }
}
