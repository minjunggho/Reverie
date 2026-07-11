import { ReactNode, createContext, useCallback, useContext, useEffect, useState } from "react";

/* ---------- Surfaces & sections ---------- */

export function Surface({ children, tight, className = "" }: {
  children: ReactNode; tight?: boolean; className?: string;
}) {
  return <div className={`surface ${tight ? "tight" : ""} ${className}`}>{children}</div>;
}

export function SectionHeader({ title, sub, action }: {
  title: string; sub?: string; action?: ReactNode;
}) {
  return (
    <div className="section-header">
      <h2>{title}</h2>
      {sub && <span className="sub">{sub}</span>}
      {action}
    </div>
  );
}

/* ---------- States ---------- */

export function LoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div role="status" aria-label="กำลังโหลด" style={{ display: "grid", gap: 10 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 52 - (i % 2) * 14 }} />
      ))}
    </div>
  );
}

export function EmptyState({ glyph = "✦", title, hint }: {
  glyph?: string; title: string; hint?: ReactNode;
}) {
  return (
    <div className="state-block">
      <span className="glyph" aria-hidden>{glyph}</span>
      <div>{title}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state-block" role="alert">
      <span className="glyph" aria-hidden>✕</span>
      <div>เกิดข้อผิดพลาดในการโหลดข้อมูล</div>
      <div className="hint">{message}</div>
      {onRetry && (
        <button className="btn small" style={{ marginTop: 14 }} onClick={onRetry}>
          ลองอีกครั้ง
        </button>
      )}
    </div>
  );
}

export function PermissionDenied() {
  return (
    <div className="state-block" role="alert">
      <span className="glyph" aria-hidden>⌘</span>
      <div>ข้อมูลนี้เปิดให้เฉพาะเจ้าของแคมเปญหรือ DM</div>
    </div>
  );
}

/* ---------- Chips & badges ---------- */

export function Chip({ children, tone = "" }: { children: ReactNode; tone?: string }) {
  return <span className={`chip ${tone}`}>{children}</span>;
}

export function VisBadge({ visibility }: { visibility: string }) {
  return <span className={`badge-vis ${visibility}`}>{visibility}</span>;
}

/* ---------- Sheet / detail panel ---------- */

export function Sheet({ title, onClose, children }: {
  title: string; onClose: () => void; children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="sheet-backdrop" onClick={onClose} role="presentation">
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title}
           onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h3>{title}</h3>
          <button className="sheet-close" onClick={onClose} aria-label="ปิด">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

/* ---------- Confirmation ---------- */

export function ConfirmDialog({ title, body, confirmLabel, danger, onConfirm, onCancel, busy }: {
  title: string; body: string; confirmLabel: string; danger?: boolean;
  onConfirm: () => void; onCancel: () => void; busy?: boolean;
}) {
  return (
    <div className="sheet-backdrop" role="presentation">
      <div className="sheet" role="alertdialog" aria-modal="true" aria-label={title}
           style={{ maxWidth: 420 }}>
        <div className="sheet-head"><h3>{title}</h3></div>
        <p style={{ color: "var(--text-2)" }}>{body}</p>
        <div style={{ display: "flex", gap: 10, marginTop: 18, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onCancel} disabled={busy}>ยกเลิก</button>
          <button className={`btn ${danger ? "danger" : "primary"}`} onClick={onConfirm}
                  disabled={busy}>
            {busy ? "กำลังดำเนินการ…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------- Segmented control ---------- */

export function Segmented<T extends string>({ options, value, onChange, ariaLabel }: {
  options: { value: T; label: string }[]; value: T; onChange: (v: T) => void;
  ariaLabel: string;
}) {
  return (
    <div className="segmented" role="tablist" aria-label={ariaLabel}>
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={o.value === value}
                className={o.value === value ? "active" : ""}
                onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---------- Toast ---------- */

interface ToastState { message: string; tone: "success" | "error" | ""; }

const ToastContext = createContext<(message: string, tone?: ToastState["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastState | null>(null);
  const show = useCallback((message: string, tone: ToastState["tone"] = "") => {
    setToast({ message, tone });
    window.setTimeout(() => setToast(null), 3500);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {toast && (
        <div className={`toast ${toast.tone}`} role="status">{toast.message}</div>
      )}
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
