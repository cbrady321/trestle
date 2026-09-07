import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { isOutcome, readSessionRows } from "../api";
import { Alert, Card } from "../components/ui";
import type { RunRow } from "../types";

type SessionView = "recent_runs" | "recent_failures";

export function SessionsPage() {
  const [view, setView] = useState<SessionView>("recent_runs");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<RunRow[]>([]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    readSessionRows(view)
      .then((envelope) => {
        if (!envelope.issued || isOutcome(envelope.body)) {
          setError(
            isOutcome(envelope.body)
              ? envelope.body.code === "projection.not_finalized"
                ? "Telemetry not filed yet for some sessions."
                : `${envelope.body.code}: ${envelope.body.message}`
              : "Failed to load sessions",
          );
          setRows([]);
          return;
        }
        setRows(envelope.body.items as RunRow[]);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Request failed");
      })
      .finally(() => setLoading(false));
  }, [view]);

  return (
    <div>
      <h1>Sessions</h1>
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
        <button
          type="button"
          className={`btn${view === "recent_runs" ? "" : " btn-muted"}`}
          onClick={() => setView("recent_runs")}
        >
          Recent runs
        </button>
        <button
          type="button"
          className={`btn${view === "recent_failures" ? "" : " btn-muted"}`}
          onClick={() => setView("recent_failures")}
        >
          Recent failures
        </button>
      </div>
      {loading && <Alert>Loading sessions…</Alert>}
      {error && <Alert variant="warn">{error}</Alert>}
      {!loading && !error && rows.length === 0 && (
        <Alert variant="warn">
          No sessions in this view. Run a plugin via MCP or CLI first.
        </Alert>
      )}
      {rows.length > 0 && (
        <Card title={view === "recent_runs" ? "Recent runs" : "Recent failures"}>
          <table className="table">
            <thead>
              <tr>
                <th>Handle</th>
                <th>Plugin</th>
                <th>State</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.run_id}>
                  <td className="mono">
                    <Link to={`/sessions/${encodeURIComponent(row.run_id)}/ask`}>
                      {row.run_id}
                    </Link>
                  </td>
                  <td>{row.plugin ?? "—"}</td>
                  <td>{row.state ?? "—"}</td>
                  <td className="mono">{row.started_at ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
