import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

const openDialogs: HTMLElement[] = [];
let rootWasInert = false;
const focusable = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

/** One modal surface: focus stays inside, Escape closes, and the opener regains focus. */
export function Dialog({ children, className, label, onClose }: {
  children: ReactNode; className: string; label: string; onClose: () => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const dialog = element.current!;
    const opener = document.activeElement as HTMLElement | null;
    const root = document.getElementById("root");
    if (!openDialogs.length && root) { rootWasInert = root.inert; root.inert = true; }
    const previous = openDialogs.at(-1);
    if (previous) previous.inert = true;
    openDialogs.push(dialog);
    const controls = () => Array.from(dialog.querySelectorAll<HTMLElement>(focusable))
      .filter((control) => control.getClientRects().length > 0 && !control.closest("[inert]"));
    (dialog.querySelector<HTMLElement>("[data-dialog-close]") || controls()[0] || dialog).focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (openDialogs.at(-1) !== dialog) return;
      if (event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); close.current();
      } else if (event.key === "Tab") {
        const items = controls();
        if (!items.length) { event.preventDefault(); dialog.focus(); return; }
        const first = items[0]; const last = items[items.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
          event.preventDefault(); first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      openDialogs.splice(openDialogs.indexOf(dialog), 1);
      const remaining = openDialogs.at(-1);
      if (remaining) remaining.inert = false;
      else if (root) root.inert = rootWasInert;
      if (opener?.isConnected && !opener.closest("[inert]")) opener.focus();
    };
  }, []);

  return createPortal(<div ref={element} className={className} role="dialog"
    aria-modal="true" aria-label={label} tabIndex={-1}>{children}</div>, document.body);
}
