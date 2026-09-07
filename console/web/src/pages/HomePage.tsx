import { useEffect, useState } from "react";
import { isOutcome, readHealth } from "../api";
import { Alert, Card } from "../components/ui";
import type { HealthBody } from "../types";

export function HomePage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthBody | null>(null);

  useEffect(() => {
    readHealth()
      .then((envelope) => {
        if (!envelope.issued || isOutcome(envelope.body)) {
          setError(
            isOutcome(envelope.body)
              ? `${envelope.body.code}: ${envelope.body.message}`
              : "Operator unreachable",
          );
          return;
        }
        setHealth(envelope.body);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to reach operator API");
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1>Operator connection</h1>
      {loading && <Alert>Checking operator API…</Alert>}
      {error && <Alert variant="error">{error}</Alert>}
      {health && (
        <Card title="Health">
          <p><strong>Reachable:</strong> {health.reachable ? "yes" : "no"}</p>
          <p><strong>Registry version:</strong> {health.registry_version}</p>
          <p><strong>Plugins:</strong> {health.plugin_count}</p>
        </Card>
      )}
      <p>
        Browse <a href="/sessions">recent sessions</a> or inspect the{" "}
        <a href="/registry">plugin registry</a>.
      </p>
      <p>
        Agent hosts: copy the <a href="/host-wiring">MCP stdio snippet</a>.
      </p>
    </div>
  );
}
