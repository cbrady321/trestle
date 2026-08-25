import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { isOutcome, readSessionRows, readTelemetryChunk } from "../api";
import { Alert, Button, Card } from "../components/ui";
import type { FetchSlice, RunRow } from "../types";

type ChunkState = {
  label: string;
  data: FetchSlice | null;
  error: string | null;
};

export function AskPage() {
  const { handle } = useParams<{ handle: string }>();
  const runId = handle ?? "";

  const [session, setSession] = useState<RunRow | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [loadingSession, setLoadingSession] = useState(true);
  const [chunk, setChunk] = useState<ChunkState | null>(null);
  const [loadingChunk, setLoadingChunk] = useState(false);

  useEffect(() => {
    if (!runId) return;
    readSessionRows("run", { run_id: runId })
      .then((envelope) => {
        if (!envelope.issued || isOutcome(envelope.body)) {
          setSessionError(
            isOutcome(envelope.body)
              ? `${envelope.body.code}: ${envelope.body.message}`
              : "Session not found",
          );
          return;
        }
        const row = envelope.body.items[0] as RunRow | undefined;
        if (row) setSession(row);
        else setSessionError("No session row returned");
      })
      .catch((err: unknown) => {
        setSessionError(err instanceof Error ? err.message : "Request failed");
      })
      .finally(() => setLoadingSession(false));
  }, [runId]);

  const fetchChunk = useCallback(
    async (label: string, window: Record<string, unknown>) => {
      setLoadingChunk(true);
      setChunk({ label, data: null, error: null });
      try {
        const envelope = await readTelemetryChunk(`${runId}/result`, window);
        if (!envelope.issued || isOutcome(envelope.body)) {
          const msg = isOutcome(envelope.body)
            ? `${envelope.body.code}: ${envelope.body.message}`
            : "Telemetry refused";
          setChunk({ label, data: null, error: msg });
          return;
        }
        setChunk({ label, data: envelope.body, error: null });
      } catch (err: unknown) {
        setChunk({
          label,
          data: null,
          error: err instanceof Error ? err.message : "Fetch failed",
        });
      } finally {
        setLoadingChunk(false);
      }
    },
    [runId],
  );

  const fetchLastError = useCallback(async () => {
    setLoadingChunk(true);
    setChunk({ label: "last_error", data: null, error: null });
    try {
      const envelope = await readSessionRows("last_error", { run_id: runId });
      if (!envelope.issued || isOutcome(envelope.body)) {
        const msg = isOutcome(envelope.body)
          ? envelope.body.code === "projection.not_finalized"
            ? "Telemetry not filed yet — evidence is still being finalized."
            : `${envelope.body.code}: ${envelope.body.message}`
          : "Query refused";
        setChunk({ label: "last_error", data: null, error: msg });
        return;
      }
      setChunk({
        label: "last_error",
        data: { values: envelope.body.items },
        error: null,
      });
    } catch (err: unknown) {
      setChunk({
        label: "last_error",
        data: null,
        error: err instanceof Error ? err.message : "Request failed",
      });
    } finally {
      setLoadingChunk(false);
    }
  }, [runId]);

  if (!runId) {
    return <Alert variant="error">Missing session handle.</Alert>;
  }

  return (
    <div>
      <p className="mono">
        <Link to="/sessions">Sessions</Link> / {runId}
      </p>
      <h1>Ask — bounded telemetry</h1>

      {loadingSession && <Alert>Loading session…</Alert>}
      {sessionError && <Alert variant="warn">{sessionError}</Alert>}
      {session && (
        <Card title="Session status">
          <p><strong>State:</strong> {session.state ?? "—"}</p>
          <p><strong>Plugin:</strong> {session.plugin ?? "—"}</p>
          <p className="mono"><strong>Started:</strong> {session.started_at ?? "—"}</p>
        </Card>
      )}

      <Card title="Retrieve telemetry">
        <p>Bounded chunks only — not a live stream.</p>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <Button
            disabled={loadingChunk}
            onClick={() => fetchChunk("result.message", { kind: "jsonpath", expr: "$.message" })}
          >
            Result message
          </Button>
          <Button
            disabled={loadingChunk}
            onClick={() => fetchChunk("result.full", { kind: "jsonpath", expr: "$" })}
          >
            Full result
          </Button>
          <Button disabled={loadingChunk} onClick={() => fetchLastError()}>
            Last error row
          </Button>
        </div>
      </Card>

      {chunk?.error && (
        <Alert variant={chunk.error.includes("not filed") ? "warn" : "error"}>
          {chunk.error}
        </Alert>
      )}

      {chunk?.data && (
        <Card title={`Chunk — ${chunk.label}`}>
          <pre className="chunk-pre mono">
            {JSON.stringify(chunk.data, null, 2)}
          </pre>
          {chunk.data.truncated && (
            <p className="meta">Truncated — narrow the window and continue.</p>
          )}
          {chunk.data.scan_bytes != null && (
            <p className="meta">Scan bytes: {chunk.data.scan_bytes}</p>
          )}
          {chunk.data.next && (
            <p className="meta">Continuation handle: {chunk.data.next}</p>
          )}
        </Card>
      )}
    </div>
  );
}
