import { useEffect, useRef, useState } from "react";
import { AppSettings, deleteModelConfig, listModelConfigs, loadAppSettings,
  markModelVerified, ModelConfig, probeModelConfig, saveAppSettings, saveModelConfig,
  SidecarConnection, storeModelCredential } from "./api";

const defaults = { openai_compatible: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com/v1", ollama: "http://127.0.0.1:11434" };
const defaultSettings: AppSettings = { manual_model_id: null, diagram_model_id: null,
  temperature: 0.3, max_output_tokens: 8192, source_strategy: "standard", auto_preview: true };

export function Settings({ connection }: { connection: SidecarConnection | null }) {
  const draftId = useRef(crypto.randomUUID());
  const [items, setItems] = useState<ModelConfig[]>([]);
  const [preferences, setPreferences] = useState<AppSettings>(defaultSettings);
  const [protocol, setProtocol] = useState<ModelConfig["protocol_id"]>("openai_compatible");
  const [name, setName] = useState(""); const [baseUrl, setBaseUrl] = useState(defaults.openai_compatible);
  const [apiKey, setApiKey] = useState(""); const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [busy, setBusy] = useState<"detect" | "save" | "preferences" | null>(null);
  const [message, setMessage] = useState("");

  async function refresh() {
    if (!connection) return;
    const [configs, settings] = await Promise.all([
      listModelConfigs(connection), loadAppSettings(connection),
    ]); setItems(configs); setPreferences(settings);
  }
  useEffect(() => { refresh().catch(() => setMessage("设置读取失败")); }, [connection]);

  async function detectModels() {
    if (!connection || !name.trim() || !baseUrl.trim() || (protocol !== "ollama" && !apiKey.trim())) {
      setMessage("请先填写连接名称、Base URL 和 API Key。"); return;
    }
    setBusy("detect"); setMessage("正在根据当前 Token 检测可用模型…");
    try {
      if (protocol !== "ollama") await storeModelCredential(draftId.current, apiKey);
      const result = await probeModelConfig({ configId: draftId.current, protocolId: protocol,
        baseUrl, modelName: "" });
      setBaseUrl(result.normalizedBaseUrl);
      setModels(result.discoveredModels); setSelectedModel(result.discoveredModels[0] ?? "");
      setMessage(result.warning ?? (result.discoveredModels.length
        ? `检测到 ${result.discoveredModels.length} 个当前 Token 可访问的模型。`
        : "服务已连接，但没有返回可用模型；请检查 Token 套餐权限。"));
    } catch (error) { setModels([]); setSelectedModel("");
      setMessage(typeof error === "string" ? error : error instanceof Error ? error.message : "模型检测失败");
    } finally { setBusy(null); }
  }

  async function saveDetectedModel() {
    if (!connection || !selectedModel) return;
    setBusy("save"); setMessage("正在保存已验证模型…");
    try {
      const id = draftId.current;
      const verification = await probeModelConfig({ configId: id, protocolId: protocol,
        baseUrl, modelName: selectedModel });
      if (!verification.modelFound) throw new Error("当前 Token 无权使用所选模型");
      await saveModelConfig(connection, { id, name, protocol_id: protocol, base_url: baseUrl,
        model_name: selectedModel, credential_ref: protocol === "ollama" ? null : id });
      await markModelVerified(connection, id);
      draftId.current = crypto.randomUUID(); setName(""); setApiKey(""); setModels([]); setSelectedModel("");
      await refresh(); setMessage("模型已保存，可在说明书页面选择。");
    } catch (error) { setMessage(typeof error === "string" ? error : error instanceof Error ? error.message : "模型保存失败"); }
    finally { setBusy(null); }
  }

  async function savePreferences() {
    if (!connection) return; setBusy("preferences");
    try { setPreferences(await saveAppSettings(connection, preferences)); setMessage("通用设置已保存。"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "通用设置保存失败"); }
    finally { setBusy(null); }
  }

  async function remove(item: ModelConfig) {
    if (!connection || !window.confirm(`删除模型配置“${item.name}”？`)) return;
    await deleteModelConfig(connection, item.id); await refresh();
  }

  const verified = items.filter((item) => item.verified_at && item.enabled);
  return <main className="settings-page"><header className="topbar"><div><p className="eyebrow">SETTINGS</p>
    <h1>设置</h1><p>模型能力按 Token 实际权限检测；API Key 只进入操作系统安全存储。</p></div></header>
    <section className="settings-content"><div className="settings-stack"><section className="model-form"><div>
      <h2>添加模型连接</h2><p>先验证连接并读取当前 Token 可访问的模型，再从检测结果中选择。</p></div>
      <label>连接名称<input required value={name} onChange={(event) => setName(event.target.value)}
        placeholder="例如：公司 DeepSeek 套餐" /></label>
      <label>协议<select value={protocol} onChange={(event) => { const value = event.target.value as typeof protocol;
        setProtocol(value); setBaseUrl(defaults[value]); setModels([]); setSelectedModel(""); }}>
        <option value="openai_compatible">OpenAI 兼容</option><option value="anthropic">Anthropic</option>
        <option value="ollama">Ollama 本地</option></select></label>
      <label>Base URL<input required value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); setModels([]); }} /></label>
      {protocol !== "ollama" && <label>API Key<input required type="password" autoComplete="new-password"
        value={apiKey} onChange={(event) => { setApiKey(event.target.value); setModels([]); }} placeholder="不会写入 SQLite" /></label>}
      <button type="button" disabled={!!busy || !connection} onClick={detectModels}>
        {busy === "detect" ? "正在检测…" : "检测可用模型"}</button>
      {models.length > 0 && <div className="detected-models"><label>当前 Token 可用模型<select value={selectedModel}
        onChange={(event) => setSelectedModel(event.target.value)}>{models.map((model) =>
        <option value={model} key={model}>{model}</option>)}</select></label>
        <button type="button" disabled={!!busy} onClick={saveDetectedModel}>
          {busy === "save" ? "正在保存…" : "保存所选模型"}</button></div>}
      {message && <p className="settings-message">{message}</p>}
    </section>
    <section className="preference-form"><div className="section-title"><span>生成与文档默认设置</span></div>
      <div className="preference-grid"><label>说明书默认模型<select value={preferences.manual_model_id ?? ""}
        onChange={(event) => setPreferences({ ...preferences, manual_model_id: event.target.value || null })}>
        <option value="">每次手动选择</option>{verified.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.model_name}</option>)}</select></label>
      <label>图表语义默认模型<select value={preferences.diagram_model_id ?? ""}
        onChange={(event) => setPreferences({ ...preferences, diagram_model_id: event.target.value || null })}>
        <option value="">跟随说明书模型</option>{verified.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.model_name}</option>)}</select></label>
      <label>生成温度<input type="number" min="0" max="2" step="0.1" value={preferences.temperature}
        onChange={(event) => setPreferences({ ...preferences, temperature: Number(event.target.value) })} /></label>
      <label>最大输出 Token<input type="number" min="1024" max="32768" step="1024" value={preferences.max_output_tokens}
        onChange={(event) => setPreferences({ ...preferences, max_output_tokens: Number(event.target.value) })} /></label>
      <label>默认源码筛选<select value={preferences.source_strategy}
        onChange={(event) => setPreferences({ ...preferences, source_strategy: event.target.value as AppSettings["source_strategy"] })}>
        <option value="standard">标准</option><option value="relaxed">宽松</option><option value="maximum">最大覆盖</option></select></label>
      <label className="check-setting"><input type="checkbox" checked={preferences.auto_preview}
        onChange={(event) => setPreferences({ ...preferences, auto_preview: event.target.checked })} />生成完成后自动打开预览</label></div>
      <button disabled={!!busy || !connection} onClick={savePreferences}>
        {busy === "preferences" ? "正在保存…" : "保存通用设置"}</button>
    </section></div>
    <div className="model-list"><div className="section-title"><span>已配置模型</span><em>{items.length}</em></div>
      {items.length ? items.map((item) => <article key={item.id}><span className={item.verified_at ? "verified" : "unverified"}>
        {item.verified_at ? "可用" : "未验证"}</span><div><strong>{item.name}</strong>
        <small>{item.protocol_id} · {item.model_name}</small><code>{item.base_url}</code></div>
        <button onClick={() => remove(item)}>删除</button></article>) : <div className="settings-empty">尚未配置模型</div>}
    </div></section></main>;
}
