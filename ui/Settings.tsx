import { FormEvent, useEffect, useState } from "react";
import { deleteModelConfig, listModelConfigs, markModelVerified, ModelConfig,
  probeModelConfig, saveModelConfig, SidecarConnection, storeModelCredential } from "./api";

const defaults = {
  openai_compatible: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com/v1",
  ollama: "http://127.0.0.1:11434",
};

export function Settings({ connection }: { connection: SidecarConnection | null }) {
  const [items, setItems] = useState<ModelConfig[]>([]);
  const [protocol, setProtocol] = useState<ModelConfig["protocol_id"]>("openai_compatible");
  const [name, setName] = useState(""); const [baseUrl, setBaseUrl] = useState(defaults.openai_compatible);
  const [model, setModel] = useState(""); const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  useEffect(() => { if (connection) listModelConfigs(connection).then(setItems)
    .catch(() => setMessage("模型配置读取失败")); }, [connection]);

  async function submit(event: FormEvent) {
    event.preventDefault(); if (!connection) return;
    const id = crypto.randomUUID(); setBusy(true); setMessage("正在安全保存并验证模型…");
    try {
      if (protocol !== "ollama") await storeModelCredential(id, apiKey);
      await saveModelConfig(connection, { id, name, protocol_id: protocol, base_url: baseUrl,
        model_name: model, credential_ref: protocol === "ollama" ? null : id });
      const result = await probeModelConfig({ configId: id, protocolId: protocol,
        baseUrl, modelName: model });
      if (!result.modelFound) throw new Error(`服务已连接，但模型列表中没有“${model}”`);
      await markModelVerified(connection, id);
      setItems(await listModelConfigs(connection)); setName(""); setModel(""); setApiKey("");
      setMessage("模型连接验证成功，已可在说明书页面选择。");
    } catch (error) {
      setItems(await listModelConfigs(connection).catch(() => items));
      setMessage(error instanceof Error ? error.message : "模型验证失败");
    } finally { setBusy(false); }
  }

  async function remove(item: ModelConfig) {
    if (!connection || !window.confirm(`删除模型配置“${item.name}”？`)) return;
    await deleteModelConfig(connection, item.id); setItems(await listModelConfigs(connection));
  }

  return <main className="settings-page"><header className="topbar"><div><p className="eyebrow">SETTINGS</p>
    <h1>设置</h1><p>配置本地或云端模型；API Key 只进入操作系统安全存储。</p></div></header>
    <section className="settings-content"><form className="model-form" onSubmit={submit}><div>
      <h2>添加模型连接</h2><p>OpenAI 兼容协议可连接多数国内外模型服务和自建网关。</p></div>
      <label>显示名称<input required value={name} onChange={(event) => setName(event.target.value)}
        placeholder="例如：DeepSeek 写作模型" /></label>
      <label>协议<select value={protocol} onChange={(event) => { const value = event.target.value as typeof protocol;
        setProtocol(value); setBaseUrl(defaults[value]); }}><option value="openai_compatible">OpenAI 兼容</option>
        <option value="anthropic">Anthropic</option><option value="ollama">Ollama 本地</option></select></label>
      <label>Base URL<input required value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
      <label>模型名称<input required value={model} onChange={(event) => setModel(event.target.value)}
        placeholder="例如：deepseek-chat" /></label>
      {protocol !== "ollama" && <label>API Key<input required type="password" autoComplete="new-password"
        value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="不会写入 SQLite" /></label>}
      <button disabled={busy || !connection}>{busy ? "正在验证…" : "保存并测试连接"}</button>
      {message && <p className="settings-message">{message}</p>}
    </form><div className="model-list"><div className="section-title"><span>已配置模型</span><em>{items.length}</em></div>
      {items.length ? items.map((item) => <article key={item.id}><span className={item.verified_at ? "verified" : "unverified"}>
        {item.verified_at ? "可用" : "未验证"}</span><div><strong>{item.name}</strong>
        <small>{item.protocol_id} · {item.model_name}</small><code>{item.base_url}</code></div>
        <button onClick={() => remove(item)}>删除</button></article>) : <div className="settings-empty">尚未配置模型</div>}
    </div></section></main>;
}
