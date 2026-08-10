import { KeyboardEvent, PointerEvent, useMemo, useRef, useState } from "react";

type Point = { x: number; y: number };
type DragState = {
  pointerId: number;
  key: string;
  group: SVGGElement;
  start: Point;
  origin: Point;
  delta: Point;
};

type Props = {
  svg: string;
  disabled?: boolean;
  onMove: (key: string, x: number, y: number) => Promise<void>;
  onSelect: (key: string | null) => void;
};

function sanitizeSvg(source: string): string {
  const document = new DOMParser().parseFromString(source, "image/svg+xml");
  if (document.querySelector("parsererror") || document.documentElement.localName !== "svg") {
    throw new Error("SVG 预览格式无效");
  }
  document.querySelectorAll("script, foreignObject").forEach((node) => node.remove());
  document.querySelectorAll("*").forEach((node) => {
    for (const attribute of Array.from(node.attributes)) {
      if (attribute.name.toLowerCase().startsWith("on")) node.removeAttribute(attribute.name);
      if (["href", "xlink:href"].includes(attribute.name.toLowerCase()) &&
          !attribute.value.startsWith("#")) node.removeAttribute(attribute.name);
    }
  });
  return document.documentElement.outerHTML;
}

function canvasPoint(container: HTMLDivElement, clientX: number, clientY: number): Point {
  const svg = container.querySelector("svg");
  const matrix = svg?.getScreenCTM();
  if (!svg || !matrix) return { x: clientX, y: clientY };
  const point = svg.createSVGPoint();
  point.x = clientX;
  point.y = clientY;
  const transformed = point.matrixTransform(matrix.inverse());
  return { x: transformed.x, y: transformed.y };
}

function constrainedPosition(group: SVGGElement, x: number, y: number): Point {
  const svg = group.ownerSVGElement;
  const viewBox = svg?.viewBox.baseVal;
  const width = Number(group.dataset.width ?? 0);
  const height = Number(group.dataset.height ?? 0);
  if (!viewBox || viewBox.width <= 0 || viewBox.height <= 0) return { x, y };
  return {
    x: Math.round(Math.min(Math.max(x, viewBox.x + 8), viewBox.x + viewBox.width - width - 8)),
    y: Math.round(Math.min(Math.max(y, viewBox.y + 76), viewBox.y + viewBox.height - height - 8)),
  };
}

export function InteractiveDiagram({ svg, disabled, onMove, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const drag = useRef<DragState | null>(null);
  const [moving, setMoving] = useState(false);
  const safeSvg = useMemo(() => sanitizeSvg(svg), [svg]);

  function pointerDown(event: PointerEvent<HTMLDivElement>) {
    if (disabled || !container.current || event.button !== 0) return;
    const target = event.target as Element;
    const group = target.closest<SVGGElement>("[data-node-key]");
    if (!group) { onSelect(null); return; }
    const key = group.dataset.nodeKey;
    if (!key) return;
    container.current.querySelectorAll(".selected-node").forEach(
      (node) => node.classList.remove("selected-node"),
    );
    const start = canvasPoint(container.current, event.clientX, event.clientY);
    drag.current = {
      pointerId: event.pointerId, key, group, start,
      origin: { x: Number(group.dataset.x ?? 0), y: Number(group.dataset.y ?? 0) },
      delta: { x: 0, y: 0 },
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    group.classList.add("dragging", "selected-node");
    group.focus();
    onSelect(key);
    setMoving(true);
    event.preventDefault();
  }

  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current || !container.current || drag.current.pointerId !== event.pointerId) return;
    const current = canvasPoint(container.current, event.clientX, event.clientY);
    drag.current.delta = {
      x: current.x - drag.current.start.x,
      y: current.y - drag.current.start.y,
    };
    drag.current.group.setAttribute(
      "transform", `translate(${drag.current.delta.x} ${drag.current.delta.y})`,
    );
  }

  async function finishDrag(event: PointerEvent<HTMLDivElement>) {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    drag.current = null;
    setMoving(false);
    current.group.classList.remove("dragging");
    const position = constrainedPosition(
      current.group,
      current.origin.x + current.delta.x,
      current.origin.y + current.delta.y,
    );
    current.group.setAttribute(
      "transform",
      `translate(${position.x - current.origin.x} ${position.y - current.origin.y})`,
    );
    if (Math.abs(current.delta.x) < 1 && Math.abs(current.delta.y) < 1) {
      current.group.removeAttribute("transform");
      return;
    }
    try {
      await onMove(current.key, position.x, position.y);
    } catch {
      current.group.removeAttribute("transform");
    }
  }

  async function keyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (disabled || event.repeat) return;
    const offsets: Record<string, Point> = {
      ArrowLeft: { x: -1, y: 0 }, ArrowRight: { x: 1, y: 0 },
      ArrowUp: { x: 0, y: -1 }, ArrowDown: { x: 0, y: 1 },
    };
    const offset = offsets[event.key];
    if (!offset) return;
    const group = (event.target as Element).closest<SVGGElement>("[data-node-key]");
    const key = group?.dataset.nodeKey;
    if (!group || !key) return;
    event.preventDefault();
    const step = event.shiftKey ? 10 : 1;
    const position = constrainedPosition(
      group,
      Number(group.dataset.x ?? 0) + offset.x * step,
      Number(group.dataset.y ?? 0) + offset.y * step,
    );
    try {
      await onMove(key, position.x, position.y);
    } catch {
      // The parent reports the stable API error and the persisted SVG remains unchanged.
    }
  }

  return <div ref={container} className={`interactive-diagram ${moving ? "is-moving" : ""}`}
    onPointerDown={pointerDown} onPointerMove={pointerMove}
    onPointerUp={finishDrag} onPointerCancel={finishDrag}
    onKeyDown={keyDown}
    dangerouslySetInnerHTML={{ __html: safeSvg }} />;
}
