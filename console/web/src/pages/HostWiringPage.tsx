import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { isOutcome, readHostWiring } from "../api";
import { Alert, Card } from "../components/ui";

export function HostWiringPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [snippet, setSnippet] = useState<string>("");

  useEffect(() => {
    readHostWiring()
      .then((envelope) => {
        if (!envelope.issued) {
          setError("Failed to load host wiring");
          return;
        }
        setSnippet(JSON.stringify(envelope.body.snippet, null, 2));
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Request failed");
      })
      .finally(() => setLoading(false));
  }, []);

  const copySnippet = async () => {
    await navigator.clipboard.writeText(snippet);
  };

  return (
    <div>
      <h1>Host wiring</h1>
      <p>Static MCP stdio snippet for agent hosts — operator HTTP does not replace stdio MCP.</p>
      {loading && <Alert>Loading snippet…</Alert>}
      {error && <Alert variant="error">{error}</Alert>}
      {!loading && !error && (
        <Card title="mcp.json snippet">
          <pre className="chunk-pre mono">{snippet}</pre>
          <p>
            <button type="button" className="btn" onClick={() => copySnippet()}>
              Copy to clipboard
            </button>
          </p>
          <p className="meta">
            Paste into your MCP host config (e.g. Cursor <code>.cursor/mcp.json</code>).
          </p>
        </Card>
      )}
      <p><Link to="/">Back to connection</Link></p>
    </div>
  );
}
