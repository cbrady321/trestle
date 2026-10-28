export interface OpsEnvelope<T = Record<string, unknown>> {
  issued: boolean;
  body: T;
}

export interface RequestOutcome {
  code: string;
  message: string;
  retryable: boolean;
  origin: string;
}

export interface HealthBody {
  reachable: boolean;
  registry_version: number;
  plugin_count: number;
}

export interface BoundedView {
  view: string;
  items: Record<string, unknown>[];
  truncated?: boolean;
  next_cursor?: string | null;
  backend?: string;
  as_of?: string;
}

export interface FetchSlice {
  truncated?: boolean;
  scan_bytes?: number;
  values?: unknown[];
  lines?: string[];
  bytes?: string;
  next?: string | null;
}

export interface RunRow {
  run_id: string;
  plugin?: string;
  state?: string;
  started_at?: string;
  ended_at?: string;
}

export interface PluginCatalogRow {
  name: string;
  version?: string;
  description?: string;
  valid?: boolean;
  capability_class?: string;
}

export interface CatalogView {
  registry_version: number;
  items: PluginCatalogRow[];
  truncated?: boolean;
  next_cursor?: string | null;
}

export interface HostWiringBody {
  transport: string;
  snippet: Record<string, unknown>;
  note: string;
}

export interface RetrievalEvent {
  label: string;
  issued: boolean;
  bytes?: number;
  at: string;
}

