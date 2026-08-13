import { useEffect, useRef, useState } from "react";

const DRAWIO_ORIGIN = "https://embed.diagrams.net";
const DRAWIO_URL = `${DRAWIO_ORIGIN}/?embed=1&proto=json&configure=1&spin=1&libraries=1` +
  "&saveAndExit=0&lang=zh&ui=kennedy&analytics=0";

type SavePayload = { xml: string; svg: string; png: string };
type SaveResult = { version: number; message: string; documentVersion: number | null;
  qaPassed: boolean | null };
type EditorMessage = { event?: string; xml?: string; data?: string; svg?: string;
  format?: string; modified?: boolean; message?: string };

export function DrawioEditor({ title, xml, onSave, onXmlChange, canUndoAi,
  hasUnconfirmedChanges, onUndoAi, onRestoreConfirmed }: {
  title: string; xml: string;
  onSave: (payload: SavePayload) => Promise<SaveResult>;
  onXmlChange: (xml: string) => void;
  canUndoAi: boolean; hasUnconfirmedChanges: boolean;
  onUndoAi: () => void; onRestoreConfirmed: () => void;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const exportRef = useRef<{ xml: string; svg: string } | null>(null);
  const savingRef = useRef(false);
  const readyRef = useRef(false);
  const lastLoadedRef = useRef("");
  const currentXmlRef = useRef(xml);
  const exportTimeoutRef = useRef<number | null>(null);
  const saveCallbackRef = useRef(onSave);
  const xmlCallbackRef = useRef(onXmlChange);
  const [status, setStatus] = useState("正在加载官方 Draw.io…");
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  saveCallbackRef.current = onSave;
  xmlCallbackRef.current = onXmlChange;

  function post(message: Record<string, unknown>) {
    iframeRef.current?.contentWindow?.postMessage(JSON.stringify(message), DRAWIO_ORIGIN);
  }

  function load(source: string) {
    lastLoadedRef.current = source;
    currentXmlRef.current = source;
    post({ action: "load", xml: source, autosave: 1, title, fit: 1,
      exportProtocol: true, saveAndExit: 0, noSaveBtn: 1, noExitBtn: 1 });
  }

  function clearExportTimeout() {
    if (exportTimeoutRef.current !== null) {
      window.clearTimeout(exportTimeoutRef.current);
      exportTimeoutRef.current = null;
    }
  }

  function failExport(message: string) {
    clearExportTimeout();
    savingRef.current = false;
    exportRef.current = null;
    setSaving(false);
    setStatus(message);
  }

  function armExportTimeout(stage: "SVG" | "PNG") {
    clearExportTimeout();
    exportTimeoutRef.current = window.setTimeout(() => {
      failExport(`Draw.io ${stage} 导出超时，当前画布未丢失，请重试确认`);
    }, 30000);
  }

  function confirmAndAssemble() {
    if (!readyRef.current || savingRef.current) return;
    const currentXml = currentXmlRef.current || xml;
    if (!currentXml) {
      setStatus("当前画布 XML 尚未就绪，请稍后重试");
      return;
    }
    savingRef.current = true;
    setSaving(true);
    exportRef.current = { xml: currentXml, svg: "" };
    setStatus("正在读取当前画布并导出 SVG…");
    armExportTimeout("SVG");
    post({ action: "export", format: "svg", xml: currentXml, embedImages: true,
      border: 20, currentPage: true });
  }

  useEffect(() => {
    if (readyRef.current && xml && xml !== lastLoadedRef.current) {
      load(xml);
      setStatus("画布已重新载入，请审阅后确认并装配");
    }
  }, [xml, title]);

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.origin !== DRAWIO_ORIGIN || event.source !== iframeRef.current?.contentWindow) return;
      let message: EditorMessage;
      try { message = typeof event.data === "string" ? JSON.parse(event.data) : event.data; }
      catch { return; }
      if (message.event === "configure") {
        post({ action: "configure", config: {
          compressXml: false, useInternalClipboard: true, preserveViewState: true,
          defaultGridSize: 10, enableCustomLibraries: true,
        } });
      } else if (message.event === "init") {
        readyRef.current = true; setReady(true); load(xml);
        setStatus("完整编辑器已就绪 · 修改会保留图层、分组与样式");
      } else if (message.event === "autosave" && message.xml) {
        currentXmlRef.current = message.xml;
        xmlCallbackRef.current(message.xml);
        setStatus("有未保存修改");
      } else if (message.event === "save" && message.xml) {
        // Keep compatibility with the editor's own save event if a future
        // Draw.io release exposes it, but the app button does not depend on it.
        if (savingRef.current) return;
        currentXmlRef.current = message.xml;
        savingRef.current = true; setSaving(true);
        xmlCallbackRef.current(message.xml);
        exportRef.current = { xml: message.xml, svg: "" };
        setStatus("正在导出 SVG…");
        armExportTimeout("SVG");
        post({ action: "export", format: "svg", xml: message.xml, embedImages: true,
          border: 20, currentPage: true });
      } else if (message.event === "export" && exportRef.current) {
        clearExportTimeout();
        if (message.format === "svg" || (!exportRef.current.svg && (message.data || message.svg))) {
          exportRef.current.svg = message.data || message.svg || "";
          setStatus("正在导出高清 PNG…");
          armExportTimeout("PNG");
          post({ action: "export", format: "png", xml: exportRef.current.xml,
            scale: 2, border: 20, currentPage: true, transparent: false });
        } else if (message.data) {
          const pending = exportRef.current;
          setStatus("正在保存 Draw.io、SVG 和 PNG 新版本…");
          void saveCallbackRef.current({ xml: pending.xml, svg: pending.svg, png: message.data })
            .then((result) => {
              clearExportTimeout();
              savingRef.current = false; setSaving(false); exportRef.current = null;
              setStatus(result.message);
              post({ action: "status", message: result.documentVersion
                ? `图表 v${result.version} 已确认，文档 v${result.documentVersion} 已装配`
                : `图表 v${result.version} 已确认` });
            })
            .catch((error) => {
              failExport(error instanceof Error ? error.message : "Draw.io 编辑结果保存失败");
              post({ action: "status", message: "保存失败，请重试", modified: true });
            });
        } else {
          failExport("Draw.io 导出结果为空，当前画布未丢失，请重试确认");
        }
      } else if (message.event === "error") {
        if (savingRef.current) failExport(message.message || "Draw.io 编辑器返回错误，当前画布未丢失");
        else setStatus(message.message || "Draw.io 编辑器返回错误");
      }
    };
    window.addEventListener("message", receive);
    return () => { window.removeEventListener("message", receive); clearExportTimeout(); };
  }, []);

  return <section className="drawio-inline-editor" aria-label={`${title} Draw.io 完整编辑器`}>
    <header><div><strong>{title}</strong><span>{status}</span></div><div>
      <span className="drawio-network-badge">Draw.io 渲染 / 微调</span>
      {canUndoAi && <button className="drawio-undo-ai" disabled={saving}
        onClick={onUndoAi}>撤销本次 AI</button>}
      {hasUnconfirmedChanges && <button className="drawio-restore-confirmed" disabled={saving}
        onClick={onRestoreConfirmed}>恢复已确认版本</button>}
      <button disabled={!ready || saving} onClick={confirmAndAssemble}>
        {saving ? "正在确认并装配…" : "确认并装配说明书"}
      </button>
    </div></header>
    <iframe ref={iframeRef} title={`${title} - Draw.io`} src={DRAWIO_URL}
      allow="clipboard-read; clipboard-write; fullscreen" />
  </section>;
}
