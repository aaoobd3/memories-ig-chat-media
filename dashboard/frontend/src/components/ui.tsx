import { useEffect, useRef, useState, type ReactNode, type ButtonHTMLAttributes } from "react";
import { Clapperboard, File, Film, Image, LayoutGrid, Loader2, X, type LucideIcon } from "lucide-react";
import type { AnyType } from "../api";
import { TYPE_COLOR } from "../lib/format";

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "brand" | "danger" | "danger-solid" | "ghost" | "default";
  size?: "sm" | "lg";
  icon?: LucideIcon;
  loading?: boolean;
  iconOnly?: boolean;
};

export function Button({ variant = "default", size, icon: Icon, loading, iconOnly, className = "", children, disabled, ...rest }: BtnProps) {
  const cls = ["btn", variant !== "default" && `btn-${variant}`, size && `btn-${size}`, iconOnly && "btn-icon", className]
    .filter(Boolean)
    .join(" ");
  const iconSize = size === "sm" ? 15 : size === "lg" ? 20 : 17;
  return (
    <button className={cls} disabled={disabled || loading} {...rest}>
      {loading ? <Loader2 size={iconSize} className="spin" /> : Icon ? <Icon size={iconSize} /> : null}
      {children}
    </button>
  );
}

export function Switch({ on, onChange, disabled, label }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={`switch ${on ? "on" : ""}`}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        onChange(!on);
      }}
    >
      <span className="track" />
      <span className="thumb" />
    </button>
  );
}

export function SwitchRow({ title, desc, on, onChange, disabled }: { title: ReactNode; desc?: ReactNode; on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <div className="switch-row">
      <div className="text">
        <div className="strong">{title}</div>
        {desc && <p>{desc}</p>}
      </div>
      <Switch on={on} onChange={onChange} disabled={disabled} label={typeof title === "string" ? title : undefined} />
    </div>
  );
}

export function Seg<T extends string | number>({ value, options, onChange }: { value: T; options: { value: T; label: ReactNode; icon?: LucideIcon }[]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="radiogroup">
      {options.map((o) => (
        <button key={String(o.value)} type="button" role="radio" aria-checked={o.value === value} className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
          {o.icon && <o.icon size={15} />}
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, desc, icon, children, footer, onClose, wide }: { title: ReactNode; desc?: ReactNode; icon?: ReactNode; children?: ReactNode; footer?: ReactNode; onClose: () => void; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true">
        <div className="modal-head">
          {icon}
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2>{title}</h2>
            {desc && <p>{desc}</p>}
          </div>
          <Button variant="ghost" size="sm" iconOnly icon={X} onClick={onClose} aria-label="Close" />
        </div>
        {children && <div className="modal-body">{children}</div>}
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Spinner({ size = 20 }: { size?: number }) {
  return <Loader2 size={size} className="spin" style={{ animation: "spin 1s linear infinite", color: "var(--text-3)" }} />;
}

export function Empty({ icon: Icon, title, children, action }: { icon: LucideIcon; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={30} />
      </div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Progress({ value, total, indeterminate }: { value?: number; total?: number; indeterminate?: boolean }) {
  const pct = total ? Math.min(100, Math.round(((value ?? 0) / total) * 100)) : 0;
  return (
    <div className={`progress ${indeterminate || !total ? "indeterminate" : ""}`}>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

export const TYPE_ICON: Record<AnyType, LucideIcon> = {
  photo: Image,
  video: Film,
  post: LayoutGrid,
  reel: Clapperboard,
  other: File,
};

export function TypeTag({ type }: { type: AnyType }) {
  const Icon = TYPE_ICON[type];
  return (
    <span className="type-tag" style={{ background: TYPE_COLOR[type] }} title={type}>
      <Icon size={14} />
    </span>
  );
}

export interface MenuItem {
  label?: string;
  icon?: LucideIcon;
  onClick?: () => void;
  danger?: boolean;
  disabled?: boolean;
  shortcut?: string;
  sep?: boolean;
}

export function ContextMenu({ x, y, items, onClose }: { x: number; y: number; items: MenuItem[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });
  useEffect(() => {
    const el = ref.current;
    if (el) {
      const r = el.getBoundingClientRect();
      setPos({ x: Math.max(8, Math.min(x, window.innerWidth - r.width - 8)), y: Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) });
    }
    const close = () => onClose();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("mousedown", close);
    window.addEventListener("blur", close);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("blur", close);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [x, y, onClose]);
  return (
    <div ref={ref} className="menu" style={{ left: pos.x, top: pos.y }} onMouseDown={(e) => e.stopPropagation()} onContextMenu={(e) => e.preventDefault()}>
      {items.map((it, i) =>
        it.sep ? (
          <div className="sep" key={i} />
        ) : (
          <button
            key={i}
            className={it.danger ? "danger" : ""}
            disabled={it.disabled}
            onClick={() => {
              onClose();
              it.onClick?.();
            }}
          >
            {it.icon && <it.icon size={16} />}
            {it.label}
            {it.shortcut && <span className="shortcut">{it.shortcut}</span>}
          </button>
        ),
      )}
    </div>
  );
}

export function StatusDot({ state }: { state: string }) {
  const cls = state === "connected" ? "dot-ok" : state === "connecting" || state === "needs_code" ? "dot-warn pulse" : state === "error" ? "dot-bad" : "dot-idle";
  return <span className={`badge-dot ${cls}`} />;
}
