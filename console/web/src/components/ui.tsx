import type { ReactNode } from "react";

export function Card({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="card">
      <h2 className="card-title">{title}</h2>
      <div className="card-body">{children}</div>
    </section>
  );
}

export function Alert({
  variant = "info",
  children,
}: {
  variant?: "info" | "warn" | "error";
  children: ReactNode;
}) {
  return <div className={`alert alert-${variant}`}>{children}</div>;
}

export function Button({
  onClick,
  disabled,
  children,
}: {
  onClick?: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <button type="button" className="btn" onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}
