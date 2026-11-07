import type { ReactNode } from "react";
import type { Tone } from "../domain/status";

export function Badge({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: Tone;
  title?: string;
}) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function InlineError({
  message,
  onRetry,
  retryLabel = "重试",
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="banner banner-error" role="alert">
      <span>{message}</span>
      {onRetry ? (
        <button type="button" className="button button-secondary" onClick={onRetry}>
          {retryLabel}
        </button>
      ) : null}
    </div>
  );
}

export function InlineNotice({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: Tone;
}) {
  return <div className={`banner banner-${tone}`}>{children}</div>;
}

export function LoadingState({ label = "加载中…" }: { label?: string }) {
  return (
    <div className="state-block" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="state-block state-empty">
      <strong>{title}</strong>
      <p>{description}</p>
      {actions ? <div className="state-actions">{actions}</div> : null}
    </div>
  );
}

export function Field({
  label,
  children,
  hint,
  htmlFor,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  htmlFor?: string;
}) {
  return (
    <label className="field" htmlFor={htmlFor}>
      <span className="field-label">{label}</span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

export function SectionTitle({
  title,
  actions,
  description,
}: {
  title: string;
  actions?: ReactNode;
  description?: string;
}) {
  return (
    <div className="section-title">
      <div>
        <h2>{title}</h2>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="section-actions">{actions}</div> : null}
    </div>
  );
}
