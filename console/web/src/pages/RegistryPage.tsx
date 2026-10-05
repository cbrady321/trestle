import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { isOutcome, iterRegistry } from "../api";
import { Alert, Card } from "../components/ui";
import type { PluginCatalogRow } from "../types";

export function RegistryPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<PluginCatalogRow[]>([]);
  const [version, setVersion] = useState<number | null>(null);

  useEffect(() => {
    iterRegistry()
      .then((envelope) => {
        if (!envelope.issued || isOutcome(envelope.body)) {
          setError(
            isOutcome(envelope.body)
              ? `${envelope.body.code}: ${envelope.body.message}`
              : "Failed to load registry",
          );
          return;
        }
        setVersion(envelope.body.registry_version);
        setRows(envelope.body.items);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Request failed");
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1>Registry</h1>
      {loading && <Alert>Loading registry…</Alert>}
      {error && <Alert variant="error">{error}</Alert>}
      {version != null && (
        <p className="meta">Registry version: {version}</p>
      )}
      {!loading && !error && rows.length === 0 && (
        <Alert variant="warn">No plugins published. Add files to your plugins directory.</Alert>
      )}
      {rows.length > 0 && (
        <Card title="Plugins">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Version</th>
                <th>Valid</th>
                <th>Class</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.name}>
                  <td>
                    <Link to={`/registry/${encodeURIComponent(row.name)}`}>
                      {row.name}
                    </Link>
                  </td>
                  <td>{row.version ?? "—"}</td>
                  <td>{row.valid ? "yes" : "no"}</td>
                  <td>{row.capability_class ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
