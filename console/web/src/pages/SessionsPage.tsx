import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { isOutcome, readSessionRows } from "../api";
import { Alert, Card } from "../components/ui";
import type { RunRow } from "../types";

export function SessionsPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<RunRow[]>([]);

  useEffect(() => {
    readSessionRows("recent_runs")
      .then((envelope) => {
        if (!envelope.issued || isOutcome(envelope.body)) {
          setError(
            isOutcome(envelope.body)
              ? `${envelope.body.code}: ${envelope.body.message}`
              : "Failed to load sessions",
          );
          return;
        }
        setRows(envelope.body.items as RunRow[]);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Request failed");
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1>Recent sessions</h1>
      {loading && <Alert>Loading sessions…</Alert>}
      {error && <Alert variant="error">{error}</Alert>}
      {!loading && !error && rows.length === 0 && (
        <Alert variant="warn">No sessions yet. Run a plugin via MCP or CLI first.</Alert>
      )}
      {rows.length > 0 && (
        <Card title="Sessions (recent_runs)">
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
