export type Row = Record<string, string | number | boolean | null>;
export type Result = {
  sql: string;
  columns: string[];
  rows: Row[];
  sources: string[];
  truncated: boolean;
  row_limit: number;
  is_synthetic: boolean;
};
export type Reply = {
  id?: number;
  status?: string;
  message: string;
  question?: string;
  result?: Result;
  as_of?: string;
  snapshot_id?: string;
};
export type Message = { id: number; role: string; payload: Reply };
export type Session = { id: string; title: string; created: string };
export type SavedMemory = { id: string; text: string; created: string };
export type Review = {
  id: string;
  title: string;
  status: "pending" | "reviewed" | "dismissed";
  created: string;
  evidence: Reply;
};
export type Catalog = {
  as_of: string;
  history_start: string;
  snapshot_id: string;
  run_id: string;
  is_synthetic: boolean;
  simulation_id: string;
  llm_configured: boolean;
  limitations: string[];
  row_counts: Record<string, number>;
  tables: Record<
    string,
    { description: string; columns: { name: string; type: string }[] }
  >;
  source_snapshots: Record<
    string,
    { dataset_id: string; version_id: string; sha256: string }
  >;
  model: { model_id: string; project_id: string; horizon_days: number } | null;
  evaluation: {
    deployment_eligible: boolean;
    test: Record<string, number>;
    selected_model: { model_type: string };
  } | null;
};
export type Overview = {
  metrics: Row;
  depots: Row[];
  trend: Row[];
  orders: Row;
};

let token = sessionStorage.getItem("dla-access-token") || "";
export const access = {
  get: () => token,
  set: (value: string) => {
    token = value;
    sessionStorage.setItem("dla-access-token", value);
  },
};
export async function api<T>(
  path: string,
  body?: unknown,
  method?: string,
): Promise<T> {
  const response = await fetch(new URL(`api/v1/${path}`, document.baseURI), {
    method: method || (body === undefined ? "GET" : "POST"),
    credentials: "same-origin",
    headers: {
      // The DataRobot port-forwarding proxy owns platform authentication. Keep
      // this workspace's access token out of its Authorization header.
      "X-DLA-App-Token": token,
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    if (response.status === 428) {
      throw new Error(
        "The forwarded request returned HTTP 428. Reopen the app using the current DataRobot Exposed ports link and retry. If this persists, inspect the failed request's response in your browser's Network tab.",
      );
    }
    const payload = await response.json().catch(() => ({}));
    throw new Error(
      typeof payload.detail === "string"
        ? payload.detail
        : `Request failed (${response.status})`,
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const query = (sql: string) => api<Result>("query", { sql });
export const literal = (value: string) => `'${value.replaceAll("'", "''")}'`;
export const label = (value: string) => value.replaceAll("_", " ");
export const number = (value: unknown) =>
  value == null
    ? "—"
    : Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
export const depot = (value: unknown) =>
  String(value).replace("SYNTHETIC_DEPOT_", "Depot ");
