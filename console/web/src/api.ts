import type {
  BoundedView,
  FetchSlice,
  HealthBody,
  OpsEnvelope,
  RequestOutcome,
} from "./types";

const OPS_BASE = "/ops/v1";

async function opsFetch<T>(path: string, init?: RequestInit): Promise<OpsEnvelope<T>> {
  const response = await fetch(`${OPS_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(`operator HTTP ${response.status}`);
  }
  return (await response.json()) as OpsEnvelope<T>;
}

export async function readHealth(): Promise<OpsEnvelope<HealthBody | RequestOutcome>> {
  return opsFetch<HealthBody | RequestOutcome>("/health");
}

export async function readSessionRows(
  view: string,
  params: Record<string, unknown> = {},
  cursor?: string | null,
): Promise<OpsEnvelope<BoundedView | RequestOutcome>> {
  return opsFetch<BoundedView | RequestOutcome>(`/sessions/${view}/rows`, {
    method: "POST",
    body: JSON.stringify({ params, cursor: cursor ?? null }),
  });
}

export async function readTelemetryChunk(
  handle: string,
  window: Record<string, unknown>,
): Promise<OpsEnvelope<FetchSlice | RequestOutcome>> {
  return opsFetch<FetchSlice | RequestOutcome>("/telemetry/chunk", {
    method: "POST",
    body: JSON.stringify({ handle, window }),
  });
}

export function isOutcome(body: unknown): body is RequestOutcome {
  return (
    typeof body === "object" &&
    body !== null &&
    "code" in body &&
    "origin" in body &&
    !("items" in body)
  );
}
