import { useEffect, useRef, useState } from "react";
import { AppSettings, deleteModelConfig, deleteModelCredential, listModelConfigs,
  loadAppSettings, ModelConfig, probeModelConfig, saveAppSettings, saveModelConfig,
  SidecarConnection, storeModelCredential } from "./api";

const defaults = { openai_compatible: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com/v1", ollama: "http://127.0.0.1:11434" };
const defaultSettings: AppSettings = { manual_model_id: null, diagram_model_id: null,
  temperature: 0.3, max_output_tokens: 8192, source_strategy: "standard", auto_preview: true };
const errorText = (error: unknown, fallback: string) => typeof error === "string"
  ? error : error instanceof Error ? error.message : fallback;

export function Settings({ connection }: { connection: SidecarConnection | null }) {
  const providerId = useRef(crypto.randomUUID());
  const [items, setItems] = useState<ModelConfig[]>([]);
  const [preferences, setPreferences] = useState<AppSettings>(defaultSettings);
  const [protocol, setProtocol] = useState<ModelConfig["protocol_id"]>("openai_compatible");
  const [name, setName] = useState(""); const [baseUrl, setBaseUrl] = useState(defaults.openai_compatible);
  const [apiKey, setApiKey] = useState(""); const [modelText, setModelText] = useState("");
  const [busy, setBusy] = useState<"detect" | "save" | "preferences" | "remove" | null>(null);
  const [message, setMessage] = useState("");

  async function refresh() {
    if (!connection) return;
    const [configs, settings] = await Promise.all([listModelConfigs(connection), loadAppSettings(connection)]);
    setItems(configs); setPreferences(settings);
  }
  useEffect(() => { refresh().catch(() => setMessage("设置读取失败")); }, [connection]);

  function enteredModels() {
    return [...new Set(modelText.split(/[\n,，]+/).map((value) => value.trim()).filter(Boolean))];
  }

  async function tryDiscover() {
    if (!connection || !baseUrl.trim() || (protocol !== "ollama" && !apiKey.trim())) {
      setMessage("请先填写 Base URL 和 API Key。也可以跳过获取，直接手工填写模型 ID。"); return;
    }
    setBusy("detect"); setMessage("正在尝试获取模型；失败不会影响手工配置…");
    try {
      if (protocol !== "ollama") await storeModelCredential(providerId.current, apiKey);
      const result = await probeModelConfig({ configId: providerId.current, protocolId: protocol,
        baseUrl, modelName: "" });
      setBaseUrl(result.normalizedBaseUrl);
      const merged = [...new Set([...enteredModels(), ...result.discoveredModels])];
      setModelText(merged.join("\n"));
      setMessage(result.warning ?? `获取到 ${result.discoveredModels.length} 个模型，可继续增删后保存。`);
    } catch (error) { setMessage(`${errorText(error, "获取模型失败")}；仍可手工填写模型 ID 后直接保存。`); }
    finally { setBusy(null); }
  }

  async function saveProvider() {
    const models = enteredModels();
    if (!connection || !name.trim() || !baseUrl.trim() || !models.length
      || (protocol !== "ollama" && !apiKey.trim())) {
      setMessage("请填写连接名称、Base URL、API Key，并至少添加一个模型 ID。"); return;
    }
    setBusy("save"); setMessage(`正在保存连接和 ${models.length} 个模型…`);
    try {
      const sharedId = providerId.current;
      if (protocol !== "ollama") await storeModelCredential(sharedId, apiKey);
      for (const model of models) {
        await saveModelConfig(connection, { id: crypto.randomUUID(), name: name.trim(),
          protocol_id: protocol, base_url: baseUrl, model_name: model,
          credential_ref: protocol === "ollama" ? null : sharedId });
      }
      providerId.current = crypto.randomUUID(); setName(""); setApiKey(""); setModelText("");
      await refresh(); setMessage(`连接已保存，共添加 ${models.length} 个模型。可稍后再做连通性验证。`);
    } catch (error) { setMessage(errorText(error, "连接保存失败")); }
    finally { setBusy(null); }
  }

  async function savePreferences() {
    if (!connection) return; setBusy("preferences");
    try { setPreferences(await saveAppSettings(connection, preferences)); setMessage("通用设置已保存。"); }
    catch (error) { setMessage(errorText(error, "通用设置保存失败")); }
    finally { setBusy(null); }
  }

  const providers = Object.values(items.reduce<Record<string, ModelConfig[]>>((result, item) => {
    (result[item.provider_id] ||= []).push(item); return result;
  }, {}));
  async function removeProvider(group: ModelConfig[]) {
    if (!connection || !window.confirm(`删除连接“${group[0].name}”及其 ${group.length} 个模型？`)) return;
    setBusy("remove");
    try {
      for (const item of group) await deleteModelConfig(connection, item.id);
      if (group[0].has_credential) await deleteModelCredential(group[0].provider_id);
      await refresh(); setMessage("连接及其模型已删除。");
    } catch (error) { setMessage(errorText(error, "删除失败")); }
    finally { setBusy(null); }
  }

  const available = items.filter((item) => item.enabled);
  return <main className="settings-page"><header className="topbar"><div><p className="eyebrow">SETTINGS</p>
    <h1>设置</h1><p>一个连接可配置多个模型；API Key 只进入操作系统安全存储。</p></div></header>
    <section className="settings-content"><div className="settings-stack"><section className="model-form"><div>
      <h2>添加模型连接</h2><p>模型 ID 以换行或逗号分隔。自动获取是可选辅助，失败也能手工保存。</p></div>
      <label>连接名称<input required value={name} onChange={(event) => setName(event.target.value)}
        placeholder="例如：SenseAudio Token Plan" /></label>
      <label>协议<select value={protocol} onChange={(event) => { const value = event.target.value as typeof protocol;
        setProtocol(value); setBaseUrl(defaults[value]); }}><option value="openai_compatible">OpenAI 兼容</option>
        <option value="anthropic">Anthropic</option><option value="ollama">Ollama 本地</option></select></label>
      <label>Base URL<input required value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
      {protocol !== "ollama" && <label>API Key<input required type="password" autoComplete="new-password"
        value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="不会写入 SQLite" /></label>}
      <label>模型 ID（每行一个）<textarea rows={6} value={modelText}
        onChange={(event) => setModelText(event.target.value)}
        placeholder={"senseaudio-s2\ndeepseek-v4-pro\nqwen3.6-27b"} /></label>
      <div className="detected-models"><button type="button" disabled={!!busy || !connection} onClick={tryDiscover}>
        {busy === "detect" ? "正在尝试获取…" : "尝试获取模型（可选）"}</button>
        <button type="button" disabled={!!busy || !connection} onClick={saveProvider}>
          {busy === "save" ? "正在保存…" : `保存连接与模型${enteredModels().length ? `（${enteredModels().length}）` : ""}`}</button></div>
      {message && <p className="settings-message">{message}</p>}
    </section>
    <section className="preference-form"><div className="section-title"><span>生成与文档默认设置</span></div>
      <div className="preference-grid"><label>说明书默认模型<select value={preferences.manual_model_id ?? ""}
        onChange={(event) => setPreferences({ ...preferences, manual_model_id: event.target.value || null })}>
        <option value="">每次手动选择</option>{available.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.model_name}</option>)}</select></label>
      <label>图表语义默认模型<select value={preferences.diagram_model_id ?? ""}
        onChange={(event) => setPreferences({ ...preferences, diagram_model_id: event.target.value || null })}>
        <option value="">跟随说明书模型</option>{available.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.model_name}</option>)}</select></label>
      <label>生成温度<input type="number" min="0" max="2" step="0.1" value={preferences.temperature}
        onChange={(event) => setPreferences({ ...preferences, temperature: Number(event.target.value) })} /></label>
      <label>最大输出 Token<input type="number" min="1024" max="32768" step="1024" value={preferences.max_output_tokens}
        onChange={(event) => setPreferences({ ...preferences, max_output_tokens: Number(event.target.value) })} /></label>
      <label>默认源码筛选<select value={preferences.source_strategy}
        onChange={(event) => setPreferences({ ...preferences, source_strategy: event.target.value as AppSettings["source_strategy"] })}>
        <option value="standard">标准</option><option value="relaxed">宽松</option><option value="maximum">最大覆盖</option></select></label>
      <label className="check-setting"><input type="checkbox" checked={preferences.auto_preview}
        onChange={(event) => setPreferences({ ...preferences, auto_preview: event.target.checked })} />生成完成后自动打开预览</label></div>
      <button disabled={!!busy || !connection} onClick={savePreferences}>{busy === "preferences" ? "正在保存…" : "保存通用设置"}</button>
    </section></div>
    <div className="model-list"><div className="section-title"><span>已配置连接</span><em>{providers.length}</em></div>
      {providers.length ? providers.map((group) => <article key={group[0].provider_id}><span className="verified">已配置</span>
        <div><strong>{group[0].name}</strong><small>{group[0].protocol_id} · {group.length} 个模型</small>
          <code>{group[0].base_url}</code><small>{group.map((item) => item.model_name).join(" · ")}</small></div>
        <button disabled={!!busy} onClick={() => removeProvider(group)}>删除连接</button></article>)
        : <div className="settings-empty">尚未配置连接</div>}
    </div></section></main>;
}
