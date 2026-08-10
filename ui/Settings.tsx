import { useEffect, useRef, useState } from "react";
import { AppSettings, deleteModelConfig, deleteModelCredential, listModelConfigs,
  hasModelCredential, loadAppSettings, ModelConfig, probeModelConfig, saveAppSettings, saveModelConfig,
  saveModelEndpointMode, SidecarConnection, storeModelCredential, testModelConnection } from "./api";

const defaults = { openai_compatible: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com/v1", ollama: "http://127.0.0.1:11434" };
type ProviderPreset = { id: string; label: string; name: string;
  protocol: ModelConfig["protocol_id"]; baseUrl: string; models: string[]; hint: string };
const providerPresets: ProviderPreset[] = [
  { id: "senseaudio", label: "商汤 SenseAudio", name: "商汤 SenseAudio", protocol: "openai_compatible",
    baseUrl: "https://api.senseaudio.cn/v1", models: ["senseaudio-s2", "deepseek-v4-pro",
      "qwen3.6-27b", "kimi-k2.6", "glm-5.2", "minimax-m2.7"], hint: "自动协商 Messages、Chat Completions 或 Responses" },
  { id: "alibaba", label: "阿里云百炼", name: "阿里云百炼", protocol: "openai_compatible",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: ["qwen3.7-plus", "qwen3.6-flash", "deepseek-v4-flash"], hint: "中国内地北京共享域名；专属空间或 Token Plan 请使用自定义" },
  { id: "deepseek", label: "DeepSeek 官方", name: "DeepSeek", protocol: "openai_compatible",
    baseUrl: "https://api.deepseek.com", models: ["deepseek-v4-pro", "deepseek-v4-flash"],
    hint: "官方 OpenAI 兼容接口" },
  { id: "longcat", label: "美团 LongCat", name: "美团 LongCat", protocol: "openai_compatible",
    baseUrl: "https://api.longcat.chat/openai/v1", models: ["LongCat-2.0"],
    hint: "LongCat 官方 OpenAI 兼容接口" },
  { id: "kimi", label: "Kimi 开放平台", name: "Kimi", protocol: "openai_compatible",
    baseUrl: "https://api.moonshot.cn/v1", models: ["kimi-k3"], hint: "Kimi 官方 Chat Completions 接口" },
  { id: "custom", label: "自定义服务商", name: "", protocol: "openai_compatible",
    baseUrl: defaults.openai_compatible, models: [], hint: "手工配置协议、请求地址和模型 ID" },
];
const defaultSettings: AppSettings = { manual_model_id: null, diagram_model_id: null,
  temperature: 0.3, max_output_tokens: 8192, source_strategy: "standard", auto_preview: true };
const errorText = (error: unknown, fallback: string) => typeof error === "string"
  ? error : error instanceof Error ? error.message : fallback;

export function Settings({ connection }: { connection: SidecarConnection | null }) {
  const providerId = useRef<string>(crypto.randomUUID());
  const [items, setItems] = useState<ModelConfig[]>([]);
  const [preferences, setPreferences] = useState<AppSettings>(defaultSettings);
  const [protocol, setProtocol] = useState<ModelConfig["protocol_id"]>("openai_compatible");
  const [name, setName] = useState(providerPresets[0].name);
  const [baseUrl, setBaseUrl] = useState(providerPresets[0].baseUrl);
  const [apiKey, setApiKey] = useState("");
  const [modelText, setModelText] = useState(providerPresets[0].models.join("\n"));
  const [busy, setBusy] = useState<"detect" | "save" | "preferences" | "remove" | null>(null);
  const [message, setMessage] = useState("");
  const [testStates, setTestStates] = useState<Record<string, string>>({});
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null);
  const [credentialStates, setCredentialStates] = useState<Record<string, boolean>>({});
  const [presetId, setPresetId] = useState("senseaudio");
  const [selectedView, setSelectedView] = useState<string>("add");
  const [addStep, setAddStep] = useState<"catalog" | "form">("catalog");
  const [loaded, setLoaded] = useState(false);

  async function refresh() {
    if (!connection) { setLoaded(false); return; }
    const [configs, settings] = await Promise.all([listModelConfigs(connection), loadAppSettings(connection)]);
    setItems(configs); setPreferences(settings);
    setSelectedView((current) => current === "add" && configs.length ? configs[0].provider_id : current);
    const providerIds = [...new Set(configs.filter((item) => item.has_credential).map((item) => item.provider_id))];
    const checks = await Promise.all(providerIds.map(async (id) => [id, await hasModelCredential(id)] as const));
    setCredentialStates(Object.fromEntries(checks));
    setLoaded(true);
  }
  useEffect(() => { setLoaded(false); refresh().catch((error) => {
    setMessage(`设置读取失败：${errorText(error, "无法连接本地数据库")}`); setLoaded(true);
  }); }, [connection]);

  function enteredModels() {
    return [...new Set(modelText.split(/[\n,，]+/).map((value) => value.trim()).filter(Boolean))];
  }

  function resetProviderForm() {
    providerId.current = crypto.randomUUID(); setEditingProviderId(null); setName("");
    setApiKey(""); applyPreset("senseaudio"); setAddStep("catalog");
  }

  function beginAddProvider() {
    resetProviderForm(); setSelectedView("add"); setMessage("");
  }

  function choosePreset(id: string) {
    providerId.current = crypto.randomUUID(); setEditingProviderId(null); setApiKey("");
    applyPreset(id); setAddStep("form"); setSelectedView("add"); setMessage("");
  }

  function applyPreset(id: string) {
    const preset = providerPresets.find((item) => item.id === id) ?? providerPresets[providerPresets.length - 1];
    setPresetId(preset.id); setName(preset.name); setProtocol(preset.protocol);
    setBaseUrl(preset.baseUrl); setModelText(preset.models.join("\n"));
  }

  async function tryDiscover() {
    if (!connection || !baseUrl.trim() || (protocol !== "ollama" && !apiKey.trim() && !editingProviderId)) {
      setMessage("请先填写 Base URL 和 API Key。也可以跳过获取，直接手工填写模型 ID。"); return;
    }
    setBusy("detect"); setMessage("正在尝试获取模型；失败不会影响手工配置…");
    try {
      if (protocol !== "ollama" && apiKey.trim()) await storeModelCredential(providerId.current, apiKey);
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
    const existing = editingProviderId
      ? items.filter((item) => item.provider_id === editingProviderId) : [];
    const hasStoredCredential = !!editingProviderId && credentialStates[editingProviderId] === true;
    if (!connection || !name.trim() || !baseUrl.trim() || !models.length
      || (protocol !== "ollama" && !apiKey.trim() && !hasStoredCredential)) {
      setMessage("请填写连接名称、Base URL、API Key，并至少添加一个模型 ID。"); return;
    }
    setBusy("save"); setMessage(`正在保存连接和 ${models.length} 个模型…`);
    try {
      const sharedId = providerId.current;
      if (protocol !== "ollama" && apiKey.trim()) await storeModelCredential(sharedId, apiKey);
      const retainedIds = new Set<string>();
      for (const model of models) {
        const previous = existing.find((item) => item.model_name === model);
        const id = previous?.id ?? crypto.randomUUID(); retainedIds.add(id);
        await saveModelConfig(connection, { id, name: name.trim(),
          protocol_id: protocol, base_url: baseUrl, model_name: model,
          credential_ref: protocol === "ollama" ? null : sharedId });
      }
      for (const item of existing) if (!retainedIds.has(item.id)) await deleteModelConfig(connection, item.id);
      if (protocol === "ollama" && hasStoredCredential) await deleteModelCredential(sharedId);
      const wasEditing = !!editingProviderId; const savedProviderId = sharedId; resetProviderForm(); await refresh();
      setSelectedView(savedProviderId);
      setMessage(`连接已${wasEditing ? "更新" : "保存"}，共配置 ${models.length} 个模型。`);
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
  async function editProvider(group: ModelConfig[]) {
    const first = group[0]; providerId.current = first.provider_id;
    setPresetId("custom");
    setEditingProviderId(first.provider_id); setName(first.name); setProtocol(first.protocol_id);
    setBaseUrl(first.base_url); setApiKey(""); setModelText(group.map((item) => item.model_name).join("\n"));
    const hasKey = !first.has_credential || await hasModelCredential(first.provider_id);
    setCredentialStates((current) => ({ ...current, [first.provider_id]: hasKey }));
    setMessage(hasKey ? "正在编辑连接。API Key 留空会保留原值，填写新 Key 才会覆盖。"
      : "系统安全存储中未找到这个连接的 API Key，请重新填写后保存。");
    setSelectedView(first.provider_id); setAddStep("form"); window.scrollTo({ top: 0, behavior: "smooth" });
  }
  async function removeProvider(group: ModelConfig[]) {
    if (!connection || !window.confirm(`删除连接“${group[0].name}”及其 ${group.length} 个模型？`)) return;
    setBusy("remove");
    try {
      for (const item of group) await deleteModelConfig(connection, item.id);
      if (group[0].has_credential) await deleteModelCredential(group[0].provider_id);
      await refresh(); setSelectedView("add"); setAddStep("catalog"); setMessage("连接及其模型已删除。");
    } catch (error) { setMessage(errorText(error, "删除失败")); }
    finally { setBusy(null); }
  }

  async function testSavedModel(item: ModelConfig) {
    setTestStates((current) => ({ ...current, [item.id]: "正在测试…" }));
    try {
      const result = await testModelConnection({ configId: item.provider_id,
        protocolId: item.protocol_id, baseUrl: item.base_url, modelName: item.model_name });
      if (connection) await saveModelEndpointMode(connection, item.id, result.endpointMode);
      setTestStates((current) => ({ ...current, [item.id]:
        `连接成功 · ${result.elapsedMs} ms · ${result.endpointMode}` }));
      await refresh();
    } catch (error) {
      setTestStates((current) => ({ ...current, [item.id]: errorText(error, "连接测试失败") }));
    }
  }

  const available = items.filter((item) => item.enabled);
  const selectedProvider = providers.find((group) => group[0].provider_id === selectedView);
  const activePreset = providerPresets.find((item) => item.id === presetId);
  const providerForm = <section className="provider-editor"><div className="editor-title">
    <button className="back-link" onClick={() => { if (editingProviderId) setEditingProviderId(null); else setAddStep("catalog"); }}>← 返回</button>
    <div><h2>{editingProviderId ? `编辑 ${name}` : `配置 ${activePreset?.label ?? "模型服务"}`}</h2>
      <p>{editingProviderId ? "修改连接信息与模型列表；API Key 留空会保留原值。" : activePreset?.hint}</p></div></div>
    <div className="connection-form-grid"><label>连接名称<input required value={name} onChange={(event) => setName(event.target.value)} /></label>
      {(presetId === "custom" || editingProviderId) && <><label>接口协议<select value={protocol} onChange={(event) => { const value = event.target.value as typeof protocol; setProtocol(value); setBaseUrl(defaults[value]); }}>
        <option value="openai_compatible">OpenAI 兼容</option><option value="anthropic">Anthropic Messages</option><option value="ollama">Ollama 本地</option></select></label>
        <label className="wide-field">请求地址（Base URL）<input required value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label></>}
      {presetId !== "custom" && !editingProviderId && <div className="preset-summary wide-field"><small>请求地址</small><strong>{baseUrl}</strong></div>}
      {protocol !== "ollama" && <label className="wide-field">API Key<input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)}
        placeholder={editingProviderId ? "留空保留现有 API Key" : "输入后将加密保存在本机"} /></label>}
      <label className="wide-field">模型 ID <small>每行一个；可随时增删</small><textarea rows={7} value={modelText} onChange={(event) => setModelText(event.target.value)} /></label></div>
    <div className="editor-actions"><button className="secondary-action" disabled={!!busy || !connection} onClick={tryDiscover}>{busy === "detect" ? "正在获取…" : "自动获取模型"}</button>
      <button disabled={!!busy || !connection} onClick={saveProvider}>{busy === "save" ? "正在保存…" : `${editingProviderId ? "保存修改" : "添加服务"}（${enteredModels().length} 个模型）`}</button></div>
  </section>;
  return <main className="settings-page"><header className="topbar"><div><p className="eyebrow">SETTINGS</p>
    <h1>模型与生成设置</h1><p>集中管理模型服务、连接状态和文档生成偏好。</p></div></header>
    <section className="settings-workbench"><aside className="provider-nav"><div className="provider-nav-title"><div><strong>模型服务</strong><small>{providers.length} 个连接</small></div>
      <button onClick={beginAddProvider}>＋ 添加</button></div>
      <div className="provider-nav-list">{!loaded ? <p>正在读取本地配置…</p> : providers.map((group) => <button className={selectedView === group[0].provider_id ? "active" : ""} key={group[0].provider_id}
        onClick={() => { setSelectedView(group[0].provider_id); setEditingProviderId(null); setMessage(""); }}><span>{group[0].name.slice(0, 1)}</span><div><strong>{group[0].name}</strong><small>{group.length} 个模型</small></div><i /></button>)}
        {loaded && !providers.length && <p>还没有模型服务<br />从常用厂商开始添加</p>}</div>
      <button className={`preference-nav ${selectedView === "preferences" ? "active" : ""}`} onClick={() => setSelectedView("preferences")}><span>⚙</span><div><strong>生成偏好</strong><small>默认模型与参数</small></div></button>
    </aside><div className="settings-detail">{message && <p className="settings-message">{message}</p>}
      {selectedView === "add" && addStep === "catalog" && <section className="provider-catalog"><div><h2>添加模型服务</h2><p>选择服务商后，只需填写 API Key；模型与地址已为你预设。</p></div>
        <div className="provider-catalog-grid">{providerPresets.map((preset) => <button key={preset.id} onClick={() => choosePreset(preset.id)}><span>{preset.id === "custom" ? "+" : preset.label.slice(0, 1)}</span>
          <div><strong>{preset.label}</strong><small>{preset.id === "custom" ? "兼容其他 OpenAI / Anthropic / Ollama 服务" : `${preset.models.length} 个推荐模型`}</small></div><b>›</b></button>)}</div></section>}
      {selectedView === "add" && addStep === "form" && providerForm}
      {selectedProvider && editingProviderId === selectedProvider[0].provider_id && providerForm}
      {selectedProvider && editingProviderId !== selectedProvider[0].provider_id && <section className="provider-detail"><header><div className="provider-identity"><span>{selectedProvider[0].name.slice(0, 1)}</span><div><h2>{selectedProvider[0].name}</h2><p>{selectedProvider[0].base_url}</p></div></div>
        <div className="provider-actions"><button onClick={() => editProvider(selectedProvider)}>编辑配置</button><button className="danger" onClick={() => removeProvider(selectedProvider)}>删除</button></div></header>
        <div className="connection-status"><span className={credentialStates[selectedProvider[0].provider_id] === false ? "warning" : "ok"} />
          <div><strong>{credentialStates[selectedProvider[0].provider_id] === false ? "需要重新配置 API Key" : "凭据已安全保存"}</strong><small>{selectedProvider[0].protocol_id} · API Key 加密存储于本机</small></div></div>
        <div className="model-table-title"><div><h3>可用模型</h3><p>逐个测试可自动识别该模型支持的接口协议。</p></div><em>{selectedProvider.length}</em></div>
        <div className="provider-models">{selectedProvider.map((item) => <div className="provider-model" key={item.id}><div><strong>{item.model_name}</strong>
          <small className={testStates[item.id]?.startsWith("连接成功") ? "test-ok" : "test-note"}>{testStates[item.id] || (item.endpoint_mode ? `已识别 · ${item.endpoint_mode}` : "尚未测试")}</small></div>
          <button disabled={testStates[item.id] === "正在测试…"} onClick={() => testSavedModel(item)}>测试连接</button></div>)}</div></section>}
      {selectedView === "preferences" && <section className="preference-form"><div className="preference-heading"><h2>生成偏好</h2><p>这些配置作为新任务的默认值，仍可在具体生成页面临时切换。</p></div>
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
    </section>}</div></section></main>;
}
