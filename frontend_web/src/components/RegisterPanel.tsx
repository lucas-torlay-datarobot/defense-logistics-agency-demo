import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import {
  depot,
  query,
  type Catalog,
  type Overview,
  type Result,
  type Row,
} from "../api";
import {
  defaults,
  PAGE_SIZE,
  registerQuery,
  sortOptions,
  type Filters,
  type RegisterKind,
} from "../registerQuery";
import { DataTable } from "./Evidence";

export default function RegisterPanel({
  kind,
  catalog,
  overview,
  initialDepot,
  onRow,
}: {
  kind: RegisterKind;
  catalog: Catalog;
  overview: Overview;
  initialDepot: string;
  onRow: (row: Row) => void;
}) {
  const [filters, setFilters] = useState<Filters>({
    ...defaults,
    location: initialDepot,
  });
  const [page, setPage] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [next, setNext] = useState(false);
  const orders = kind === "orders";
  const change = (key: keyof Filters, value: string) => {
    setFilters((f) => ({ ...f, [key]: value }));
    setPage(0);
  };
  useEffect(() => {
    let active = true;
    setBusy(true);
    setError("");
    setResult(null);
    setNext(false);
    const timer = setTimeout(async () => {
      try {
        const response = await query(
          registerQuery(kind, filters, catalog.as_of, page),
        );
        if (active) {
          setNext(response.rows.length > PAGE_SIZE);
          setResult({ ...response, rows: response.rows.slice(0, PAGE_SIZE) });
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        if (active) setBusy(false);
      }
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [kind, filters, catalog.as_of, page]);
  const input = (
    key: keyof Filters,
    label: string,
    type = "number",
    max?: number,
  ) => (
    <label>
      {label}
      <input
        aria-label={label}
        type={type}
        min={type === "number" ? 0 : undefined}
        max={max}
        step={type === "number" ? "any" : undefined}
        value={filters[key]}
        onChange={(e) => change(key, e.target.value)}
      />
    </label>
  );
  return (
    <section className="panel inventory-panel">
      <div className="panel-heading">
        <div>
          <h2>
            {orders
              ? "Order register"
              : kind === "overview"
                ? "Shortage risk watchlist"
                : "Item-location register"}
          </h2>
          <p>
            {orders
              ? `All orders, including received and future-due supply. Status is as of ${catalog.as_of}. Stock and risk describe the current snapshot, not the order date.`
              : "Coverage is a trailing-demand ratio. Risk estimates any shortage in the next 14 days."}
          </p>
        </div>
      </div>
      <div className="register-filters">
        {input(
          "search",
          orders
            ? "Item name, NIIN, NSN or order ID"
            : "Item name, NIIN or NSN",
          "search",
        )}
        <label>
          Depot
          <select
            aria-label="Filter depot"
            value={filters.location}
            onChange={(e) => change("location", e.target.value)}
          >
            <option value="">All depots</option>
            {overview.depots.map((d) => (
              <option key={String(d.location)} value={String(d.location)}>
                {depot(d.location)}
              </option>
            ))}
          </select>
        </label>
        {orders ? (
          <>
            <label>
              Order status
              <select
                aria-label="Order status"
                value={filters.status}
                onChange={(e) => change("status", e.target.value)}
              >
                <option value="all">All orders</option>
                <option value="open">Open</option>
                <option value="received">Received</option>
              </select>
            </label>
            <label>
              Due status
              <select
                aria-label="Due status"
                value={filters.due}
                onChange={(e) => change("due", e.target.value)}
              >
                <option value="all">All expected dates</option>
                <option value="overdue">Open and overdue</option>
                <option value="future">Open and due after snapshot</option>
              </select>
            </label>
            {input("dateFrom", "Expected receipt from", "date")}
            {input("dateTo", "Expected receipt through", "date")}
            {input("minQuantity", "Minimum order quantity")}
            {input("maxQuantity", "Maximum order quantity")}
          </>
        ) : (
          <>
            <label>
              Stock / scoring
              <select
                aria-label="Stock or scoring filter"
                value={filters.stock}
                onChange={(e) => change("stock", e.target.value)}
              >
                <option value="all">All positions</option>
                <option value="zero">Zero stock</option>
                <option value="positive">Positive stock</option>
                <option value="unscored">Risk not assessed</option>
              </select>
            </label>
            {input("maxCover", "Maximum days of cover")}
          </>
        )}
        {input("minRisk", "Minimum risk (%)", "number", 100)}
        {input("maxRisk", "Maximum risk (%)", "number", 100)}
        <label>
          Sort by
          <select
            aria-label="Sort by"
            value={
              filters.sort ||
              (orders ? "order_date" : "shortage_probability_14d")
            }
            onChange={(e) => change("sort", e.target.value)}
          >
            {Object.entries(sortOptions(kind)).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Direction
          <select
            aria-label="Sort direction"
            value={filters.direction}
            onChange={(e) => change("direction", e.target.value)}
          >
            <option value="desc">Descending</option>
            <option value="asc">Ascending</option>
          </select>
        </label>
        <button
          className="text-button"
          onClick={() => {
            setFilters({ ...defaults });
            setPage(0);
          }}
        >
          Reset filters
        </button>
      </div>
      {!catalog.row_counts.risk_scores && (
        <div className="note warning">
          Model scores are unavailable. Risk filters exclude unscored rows.
        </div>
      )}
      {error && (
        <div className="note warning" role="alert">
          {error}
        </div>
      )}
      {busy ? (
        <div className="empty">
          <LoaderCircle className="spin" size={22} />
        </div>
      ) : (
        result && <DataTable result={result} onRow={onRow} />
      )}
      <div className="panel-footer register-pagination">
        <span>
          {busy
            ? "Loading…"
            : result?.rows.length
              ? `Rows ${page * PAGE_SIZE + 1}–${page * PAGE_SIZE + result.rows.length} · page ${page + 1}`
              : "No matching rows"}{" "}
          · Synthetic operations
        </span>
        <button
          className="text-button"
          disabled={busy || page === 0}
          onClick={() => setPage((p) => p - 1)}
        >
          Previous
        </button>
        <button
          className="text-button"
          disabled={busy || !next}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </button>
      </div>
    </section>
  );
}
