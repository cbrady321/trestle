import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { describeRegistryEntry, isOutcome } from "../api";
import { Alert, Card } from "../components/ui";

export function RegistryDetailPage() {
  const { pluginId } = useParams<{ pluginId: string }>();
  const id = pluginId ?? "";

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (!id) return;
    describeRegistryEntry(id)
      .then((envelope) => {
        if (!envelope.issued || isOutcome(envelope.body)) {
          setError(
            isOutcome(envelope.body)
              ? `${envelope.body.code}: ${envelope.body.message}`
              : "Plugin not found",
          );
          return;
        }
        setDetail(envelope.body);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Request failed");
      })
      .finally(() => setLoading(false));
  }, [id]);

  if (!id) {
    return <Alert variant="error">Missing plugin id.</Alert>;
  }

  return (
    <div>
      <p className="mono">
        <Link to="/registry">Registry</Link> / {id}
      </p>
      <h1>{id}</h1>
      {loading && <Alert>Loading plugin…</Alert>}
      {error && <Alert variant="error">{error}</Alert>}
      {detail && (
        <Card title="Description">
          <pre className="chunk-pre mono">{JSON.stringify(detail, null, 2)}</pre>
        </Card>
      )}
    </div>
  );
}
