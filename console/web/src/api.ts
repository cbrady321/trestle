import type {
  BoundedView,
  CatalogView,
  FetchSlice,
  HealthBody,
  HostWiringBody,
  OpsEnvelope,
  RequestOutcome,
  RunRow,
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

export async function readHostWiring(): Promise<OpsEnvelope<HostWiringBody>> {
  return opsFetch<HostWiringBody>("/host_wiring");
}

export async function iterRegistry(): Promise<OpsEnvelope<CatalogView | RequestOutcome>> {
  return opsFetch<CatalogView | RequestOutcome>("/registry");
}

export async function describeRegistryEntry(
  pluginId: string,
): Promise<OpsEnvelope<Record<string, unknown> | RequestOutcome>> {
  return opsFetch<Record<string, unknown> | RequestOutcome>(
    `/registry/${encodeURIComponent(pluginId)}`,
  );
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

export async function cancelRun(handle: string): Promise<OpsEnvelope<RequestOutcome>> {
  return opsFetch<RequestOutcome>("/actions/cancel", {
    method: "POST",
    body: JSON.stringify({ handle }),
  });
}

export async function pinRetention(handle: string): Promise<OpsEnvelope<RequestOutcome>> {
  return opsFetch<RequestOutcome>(`/retention/${encodeURIComponent(handle)}`, {
    method: "PUT",
  });
}

export async function unpinRetention(handle: string): Promise<OpsEnvelope<RequestOutcome>> {
  return opsFetch<RequestOutcome>(`/retention/${encodeURIComponent(handle)}`, {
    method: "DELETE",
  });
}

export async function joinWaits(
  handles: string[],
  mode = "all",
  timeoutMs = 2000,
): Promise<OpsEnvelope<{ items: RunRow[] } | RequestOutcome>> {
  return opsFetch<{ items: RunRow[] } | RequestOutcome>("/waits/join", {
    method: "POST",
    body: JSON.stringify({ handles, mode, timeout_ms: timeoutMs }),
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
