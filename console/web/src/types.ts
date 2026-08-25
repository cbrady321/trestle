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
