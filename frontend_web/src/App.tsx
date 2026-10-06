import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  Boxes,
  ChevronRight,
  Database,
  LayoutDashboard,
  LogOut,
  Moon,
  Play,
  ShieldCheck,
  Sparkles,
  Sun,
  Truck,
  BookmarkCheck,
  Brain,
  Trash2,
  KeyRound,
} from "lucide-react";
import {
  access,
  api,
  depot,
  number,
  query,
  type Catalog,
  type Overview,
  type Result,
  type Review,
  type Row,
  type SavedMemory,
} from "./api";
import AgentPanel from "./components/AgentPanel";
import RegisterPanel from "./components/RegisterPanel";
import { Evidence } from "./components/Evidence";

type Tab =
  "overview" | "inventory" | "orders" | "explorer" | "reviews" | "memory";
const nav = [
  { id: "overview", title: "Overview", icon: LayoutDashboard },
  { id: "inventory", title: "Inventory", icon: Boxes },
  { id: "orders", title: "Orders", icon: Truck },
  { id: "explorer", title: "Explore", icon: Database },
  { id: "reviews", title: "Review", icon: BookmarkCheck },
  { id: "memory", title: "Memory", icon: Brain },
] as const;
const presets = {
  "Highest model risk":
    "SELECT niin, item_name, location, closing_stock, days_of_cover, shortage_probability_14d FROM latest_inventory WHERE shortage_probability_14d IS NOT NULL ORDER BY shortage_probability_14d DESC, niin, location LIMIT 25",
  "Overdue orders":
    "SELECT order_id, niin, destination, quantity, order_date, expected_receipt_date FROM replenishment_orders WHERE is_overdue ORDER BY expected_receipt_date LIMIT 50",
  "Shortage frequency":
    "SELECT location, count(*) AS item_days, count(*) FILTER (WHERE unfulfilled_quantity > 0) AS shortage_item_days, round(100.0 * count(*) FILTER (WHERE unfulfilled_quantity > 0) / count(*), 2) AS shortage_percent FROM daily_inventory GROUP BY location ORDER BY shortage_percent DESC",
};
function Login({ onConnect }: { onConnect: (token: string) => Promise<void> }) {
  const [token, setToken] = useState(access.get());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="login-screen">
      <form
        className="login-card"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await onConnect(token);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="brand-icon">
          <Boxes size={26} />
        </div>
        <span className="eyebrow">DLA • Logistics Intelligence</span>
        <h1>
          Know where to
          <br />
          look next.
        </h1>
        <p>
          A workspace for inventory, incoming supply, and model-informed
          shortage review.
        </p>
        <label htmlFor="access">Workspace access token</label>
        <div className="token-field">
          <KeyRound size={17} />
          <input
            id="access"
            type="password"
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="DLA_APP_ACCESS_TOKEN"
            required
          />
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <button className="button primary" disabled={busy}>
          {busy ? "Connecting…" : "Open workspace"}
          <ArrowUpRight size={17} />
        </button>
        <div className="note">
          Synthetic operations • real catalog identities
          <br />
          Single-user demonstration workspace
        </div>
      </form>
    </div>
  );
}
function Stat({
  name,
  value,
  note,
  accent = false,
}: {
  name: string;
  value: string;
  note: string;
  accent?: boolean;
}) {
  return (
    <div className={`stat panel ${accent ? "accent-stat" : ""}`}>
      <span>{name}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}
function Trend({ rows }: { rows: Row[] }) {
  const max = Math.max(10, ...rows.map((r) => Number(r.shortage_pct)));
  const points = rows
    .map(
      (r, i) =>
        `${40 + (i * 610) / Math.max(1, rows.length - 1)},${160 - (Number(r.shortage_pct) * 130) / max}`,
    )
    .join(" ");
  return (
    <div className="trend">
      <svg
        viewBox="0 0 680 195"
        role="img"
        aria-label="Observed percentage of item-location rows with unfulfilled demand over the last 28 days"
      >
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line
              x1="40"
              x2="650"
              y1={160 - 130 * t}
              y2={160 - 130 * t}
              stroke="var(--border)"
              strokeDasharray="4 5"
            />
            <text x="2" y={164 - 130 * t} fill="var(--muted)" fontSize="10">
              {Math.round(max * t)}%
            </text>
          </g>
        ))}
        <polygon
          points={`40,160 ${points} 650,160`}
          fill="var(--accent)"
          opacity="0.08"
        />
        <polyline
          points={points}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2.5"
        />
        <text x="40" y="188" fill="var(--muted)" fontSize="10">
          {String(rows[0]?.date || "").slice(0, 10)}
        </text>
        <text x="590" y="188" fill="var(--muted)" fontSize="10">
          {String(rows.at(-1)?.date || "").slice(0, 10)}
        </text>
      </svg>
    </div>
  );
}
export default function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [agentOpen, setAgentOpen] = useState(true);
  const [prompt, setPrompt] = useState("");
  const [light, setLight] = useState(
    localStorage.getItem("dla-theme") === "light",
  );
  const [error, setError] = useState("");
  const [queryBusy, setQueryBusy] = useState(false);
  const [location, setLocation] = useState("");
  const [sql, setSQL] = useState<string>(presets["Highest model risk"]);
  const [explored, setExplored] = useState<Result | null>(null);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [memories, setMemories] = useState<SavedMemory[]>([]);
  const [preference, setPreference] = useState("");
  const clearPrompt = useCallback(() => setPrompt(""), []);
  useEffect(() => {
    document.documentElement.dataset.theme = light ? "light" : "dark";
    localStorage.setItem("dla-theme", light ? "light" : "dark");
  }, [light]);
  async function connect(token: string) {
    access.set(token);
    const [c, o] = await Promise.all([
      api<Catalog>("catalog"),
      api<Overview>("overview"),
    ]);
    setCatalog(c);
    setOverview(o);
  }
  const refreshReviews = useCallback(() => {
    api<Review[]>("reviews")
      .then(setReviews)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (catalog) {
      refreshReviews();
      api<SavedMemory[]>("memories")
        .then(setMemories)
        .catch((e) => setError(e.message));
    }
  }, [catalog, refreshReviews]);
  function ask(question: string) {
    setAgentOpen(true);
    setPrompt(question);
  }
  function investigate(row: Row) {
    ask(
      `For NIIN ${row.niin} at ${row.location || row.destination}, show current stock, 14-day shortage probability if available, and open orders. Explain what the evidence supports and what it cannot tell us.`,
    );
  }
  async function runSQL() {
    setQueryBusy(true);
    setError("");
    setExplored(null);
    try {
      setExplored(await query(sql));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setQueryBusy(false);
    }
  }
  async function addMemory() {
    if (!preference.trim()) return;
    try {
      await api("memories", { text: preference });
      setPreference("");
      setMemories(await api<SavedMemory[]>("memories"));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const pending = reviews.filter((r) => r.status === "pending").length;
  if (!catalog || !overview) return <Login onConnect={connect} />;
  return (
    <div className={`app ${agentOpen ? "" : "agent-closed"}`}>
      <header className="app-header">
        <div className="brand">
          <div className="brand-icon">
            <Boxes size={22} />
          </div>
          <div>
            <strong>
              DLA <span>Logistics Intelligence</span>
            </strong>
            <small>DEFENSE LOGISTICS DEMONSTRATION</small>
          </div>
        </div>
        <div className="header-actions">
          <span className="pill synthetic">
            <span /> SYNTHETIC SCENARIO
          </span>
          <button
            className="icon-button"
            aria-label="Toggle color theme"
            onClick={() => setLight(!light)}
          >
            {light ? <Moon size={18} /> : <Sun size={18} />}
          </button>
          <button
            className="button primary ask-top"
            onClick={() => setAgentOpen(!agentOpen)}
          >
            <Sparkles size={15} /> Ask DLA
          </button>
          <button
            className="icon-button"
            aria-label="Lock workspace"
            onClick={() => {
              access.set("");
              setCatalog(null);
            }}
          >
            <LogOut size={16} />
          </button>
          <span className="avatar">OP</span>
        </div>
      </header>
      <div className="context-strip">
        <span>
          <ShieldCheck size={13} /> Decision support workspace
        </span>
        <ChevronRight size={12} />
        <span>Inventory readiness</span>
        <div className="context-right">
          <span className="status-dot" /> Snapshot {catalog.as_of}
          <span className="separator">|</span>
          {catalog.row_counts.risk_scores
            ? "DataRobot model scores loaded"
            : "Model scores not loaded"}
        </div>
      </div>
      <nav className="nav-rail" aria-label="Main navigation">
        {nav.map((n) => (
          <button
            className={tab === n.id ? "active" : ""}
            key={n.id}
            onClick={() => {
              setTab(n.id);
              setError("");
            }}
            aria-label={n.title}
            aria-current={tab === n.id ? "page" : undefined}
          >
            <n.icon size={20} />
            <span>{n.title}</span>
            {n.id === "reviews" && pending > 0 && <i>{pending}</i>}
          </button>
        ))}
        <div className="rail-bottom">
          <ShieldCheck size={17} />
          <span>DEMO</span>
        </div>
      </nav>
      <main className="workspace">
        <div className="page-heading">
          <div>
            <span className="eyebrow">
              OPERATIONS / {nav.find((n) => n.id === tab)?.title.toUpperCase()}
            </span>
            <h1>
              {
                {
                  overview: "Logistics at a glance",
                  inventory: "Inventory & shortage risk",
                  orders: "Incoming supply",
                  explorer: "Explore your data",
                  reviews: "Evidence review queue",
                  memory: "Memory & provenance",
                }[tab]
              }
            </h1>
            <p>
              {
                {
                  overview:
                    "Find the pressure points. Investigate the evidence. Decide what to review.",
                  inventory:
                    "End-of-day inventory with the selected model’s next-14-day shortage probabilities.",
                  orders:
                    "Open replenishment orders at the scenario snapshot date.",
                  explorer:
                    "Ask an adaptable question, or inspect the same data directly with SQL.",
                  reviews:
                    "Saved query evidence for human review. No operational orders are executed.",
                  memory:
                    "Persistent preferences, dataset lineage, and model scope.",
                }[tab]
              }
            </p>
          </div>
          <span className="snapshot-tag">
            AS OF<strong>{catalog.as_of}</strong>
          </span>
        </div>
        {error && (
          <div className="error error-banner" role="alert">
            {error}
          </div>
        )}
        {tab === "overview" && (
          <>
            {catalog.evaluation && !catalog.evaluation.deployment_eligible && (
              <div className="note warning">
                The selected model did not pass all baseline gates. Scores are
                available for investigation only; inspect Model & limits before
                using this demonstration.
              </div>
            )}
            <div className="stats">
              <Stat
                name="Item-location positions"
                value={number(overview.metrics.item_locations)}
                note={`${number(overview.metrics.items)} items · ${number(overview.metrics.locations)} simulated depots`}
              />
              <Stat
                name="Elevated shortage risk"
                value={
                  Number(overview.metrics.scored)
                    ? number(overview.metrics.elevated_risk)
                    : "—"
                }
                note="Model probability ≥ 50% · next 14 days"
                accent
              />
              <Stat
                name="Zero closing stock"
                value={number(overview.metrics.zero_stock)}
                note="Observed at the snapshot date"
              />
              <Stat
                name="Overdue replenishments"
                value={number(overview.orders.overdue_orders)}
                note={`${number(overview.orders.open_orders)} open orders in total`}
              />
            </div>
            <div className="overview-charts">
              <section className="panel trend-panel">
                <div className="panel-heading">
                  <div>
                    <h2>Observed shortage incidence</h2>
                    <p>
                      Share of item-location days with unmet demand · last 28
                      days
                    </p>
                  </div>
                  <span className="pill muted-pill">Historical</span>
                </div>
                <Trend rows={overview.trend} />
              </section>
              <section className="panel depots">
                <div className="panel-heading">
                  <h2>Depot watch</h2>
                  <Activity size={16} />
                </div>
                {overview.depots.map((d) => (
                  <button
                    className="depot-row"
                    key={String(d.location)}
                    onClick={() => {
                      setLocation(String(d.location));
                      setTab("inventory");
                    }}
                  >
                    <div>
                      <strong>{depot(d.location)}</strong>
                      <small>
                        {number(d.zero_stock)} positions with zero stock
                      </small>
                    </div>
                    <div className="depot-risk">
                      {d.mean_risk == null
                        ? "—"
                        : `${(Number(d.mean_risk) * 100).toFixed(0)}%`}
                      <small>mean risk</small>
                    </div>
                    <ChevronRight size={15} />
                  </button>
                ))}
              </section>
            </div>
          </>
        )}
        {(tab === "overview" || tab === "inventory" || tab === "orders") && (
          <RegisterPanel
            key={tab + location}
            kind={tab}
            catalog={catalog}
            overview={overview}
            initialDepot={location}
            onRow={investigate}
          />
        )}
        {tab === "explorer" && (
          <>
            <div className="explore-callout panel">
              <div className="agent-mark">
                <Sparkles size={20} />
              </div>
              <div>
                <h2>Questions aren’t limited to a menu.</h2>
                <p>
                  The assistant uses the available schema to build a query.
                  Every data answer includes inspectable SQL and rows.
                </p>
              </div>
              <button
                className="button primary"
                onClick={() =>
                  ask(
                    "Which NIINs had shortages at more than one depot during the last 28 days?",
                  )
                }
              >
                Ask a question <ArrowUpRight size={16} />
              </button>
            </div>
            <section className="panel">
              <div className="panel-heading">
                <h2>SQL workbench</h2>
                <span className="pill muted-pill">Read only · 200-row cap</span>
              </div>
              <div className="preset-tabs">
                {Object.entries(presets).map(([name, value]) => (
                  <button key={name} onClick={() => setSQL(value)}>
                    {name}
                  </button>
                ))}
              </div>
              <textarea
                className="sql-editor"
                aria-label="SQL query"
                spellCheck={false}
                value={sql}
                onChange={(e) => setSQL(e.target.value)}
                rows={7}
              />
              <div className="panel-footer">
                <span>
                  Available tables: {Object.keys(catalog.tables).join(", ")}
                </span>
                <button
                  className="button primary"
                  disabled={queryBusy}
                  onClick={runSQL}
                >
                  <Play size={14} />
                  {queryBusy ? "Running…" : "Run query"}
                </button>
              </div>
              {explored && <Evidence result={explored} />}
            </section>
            <section className="schema-grid">
              {Object.entries(catalog.tables).map(([name, t]) => (
                <details className="panel schema" key={name}>
                  <summary>
                    <Database size={15} />
                    {name}
                    <span>{number(catalog.row_counts[name])} rows</span>
                  </summary>
                  <p>{t.description}</p>
                  <div className="column-list">
                    {t.columns.map((c) => (
                      <div key={c.name}>
                        <code>{c.name}</code>
                        <small>{c.type}</small>
                      </div>
                    ))}
                  </div>
                </details>
              ))}
            </section>
          </>
        )}
        {tab === "reviews" && (
          <section className="review-list">
            {!reviews.length && (
              <div className="panel empty large">
                <BookmarkCheck size={32} />
                <h2>Keep the evidence that matters.</h2>
                <p>
                  Use “Save for review” on an assistant query result to add it
                  here.
                </p>
              </div>
            )}
            {reviews.map((r) => (
              <article className="panel review-card" key={r.id}>
                <div className="panel-heading">
                  <div>
                    <span
                      className={`pill ${r.status === "pending" ? "synthetic" : "muted-pill"}`}
                    >
                      {r.status}
                    </span>
                    <h2>{r.title}</h2>
                    <p>
                      Saved {r.created.slice(0, 10)} · scenario{" "}
                      {r.evidence.as_of}
                    </p>
                  </div>
                  <select
                    aria-label="Review status"
                    value={r.status}
                    onChange={async (e) => {
                      try {
                        await api(
                          `reviews/${r.id}`,
                          { status: e.target.value },
                          "PATCH",
                        );
                        refreshReviews();
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    <option value="pending">Pending</option>
                    <option value="reviewed">Reviewed</option>
                    <option value="dismissed">Dismissed</option>
                  </select>
                </div>
                <p className="review-answer">{r.evidence.message}</p>
                {r.evidence.result && <Evidence result={r.evidence.result} />}
              </article>
            ))}
          </section>
        )}
        {tab === "memory" && (
          <>
            <section className="panel memory-panel">
              <div className="panel-heading">
                <div>
                  <h2>Remembered preferences</h2>
                  <p>
                    You choose what persists across conversations. Preferences
                    provide context; they do not override data or query
                    restrictions.
                  </p>
                </div>
                <Brain size={20} />
              </div>
              <div className="memory-form">
                <input
                  aria-label="Preference to remember"
                  placeholder="For example: focus on Depot B unless I specify otherwise."
                  maxLength={600}
                  value={preference}
                  onChange={(e) => setPreference(e.target.value)}
                />
                <button
                  className="button primary"
                  onClick={addMemory}
                  disabled={!preference.trim()}
                >
                  Remember
                </button>
              </div>
              {!memories.length && (
                <p className="empty">No saved preferences yet.</p>
              )}
              {memories.map((m) => (
                <div className="memory-row" key={m.id}>
                  <span>{m.text}</span>
                  <button
                    className="icon-button"
                    aria-label={`Forget ${m.text}`}
                    onClick={async () => {
                      try {
                        await api(`memories/${m.id}`, undefined, "DELETE");
                        setMemories(await api<SavedMemory[]>("memories"));
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
            </section>
            <section className="panel provenance">
              <div className="panel-heading">
                <h2>Dataset provenance</h2>
                <Database size={18} />
              </div>
              <p>
                Scenario <code>{catalog.simulation_id}</code>
              </p>
              <p>
                Observed history: {catalog.history_start} → {catalog.as_of}.
                Snapshot <code>{catalog.snapshot_id.slice(0, 8)}</code>.
              </p>
              {Object.entries(catalog.source_snapshots).map(([name, s]) => (
                <div className="source-record" key={name}>
                  <strong>{name}</strong>
                  <div>
                    Dataset <code>{s.dataset_id}</code>
                  </div>
                  <div>
                    Version <code>{s.version_id}</code>
                  </div>
                  <details>
                    <summary>Content hash</summary>
                    <code>{s.sha256}</code>
                  </details>
                </div>
              ))}
            </section>
            <section className="panel provenance">
              <div className="panel-heading">
                <h2>Model & limits</h2>
                <ShieldCheck size={18} />
              </div>
              {catalog.model ? (
                <>
                  <p>
                    {catalog.evaluation?.selected_model.model_type ||
                      "Selected DataRobot model"}
                  </p>
                  <p>
                    Project <code>{catalog.model.project_id}</code>
                    <br />
                    Model <code>{catalog.model.model_id}</code>
                  </p>
                  {catalog.evaluation && (
                    <div className="model-metrics">
                      <span>
                        Final test AP{" "}
                        <strong>
                          {catalog.evaluation.test.average_precision?.toFixed(
                            3,
                          )}
                        </strong>
                      </span>
                      <span>
                        Final test ROC AUC{" "}
                        <strong>
                          {catalog.evaluation.test.roc_auc?.toFixed(3)}
                        </strong>
                      </span>
                      <span>
                        Baseline gates{" "}
                        <strong>
                          {catalog.evaluation.deployment_eligible
                            ? "Passed"
                            : "Not passed"}
                        </strong>
                      </span>
                    </div>
                  )}
                </>
              ) : (
                <p>No completed model contract loaded.</p>
              )}
              <ul>
                {catalog.limitations.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
              <p className="note">
                Memory is saved in this workspace’s persistent storage. This
                build is a single-user Codespace app; a shared production
                deployment needs identity-scoped storage and access controls.
              </p>
            </section>
          </>
        )}
        <footer className="workspace-footer">
          <span>
            <ShieldCheck size={12} /> SYNTHETIC DEMONSTRATION · HUMAN REVIEW
            REQUIRED
          </span>
          <span>Powered by DataRobot</span>
        </footer>
      </main>
      {agentOpen && (
        <AgentPanel
          catalog={catalog}
          prompt={prompt}
          clearPrompt={clearPrompt}
          close={() => setAgentOpen(false)}
          onReview={() => {
            refreshReviews();
            setTab("reviews");
          }}
        />
      )}
    </div>
  );
}
