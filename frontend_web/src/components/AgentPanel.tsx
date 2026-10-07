import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Bot,
  Plus,
  X,
  LoaderCircle,
  Trash2,
  History,
} from "lucide-react";
import {
  api,
  profileId,
  type Catalog,
  type Message,
  type Reply,
  type Session,
} from "../api";
import { Evidence } from "./Evidence";
import { EmailAction } from "./EmailAction";
import { DocumentEvidence } from "./DocumentEvidence";

const suggestions = [
  "Which items have the highest 14-day shortage probability?",
  "Which open replenishment orders are overdue?",
  "What guidance do the documents give for overdue replenishment?",
  "Show overdue orders and retrieve guidance for following up on them.",
];
export default function AgentPanel({
  catalog,
  prompt,
  clearPrompt,
  close,
  onReview,
}: {
  catalog: Catalog;
  prompt: string;
  clearPrompt: () => void;
  close: () => void;
  onReview: () => void;
}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [session, setSession] = useState(
    sessionStorage.getItem(`dla-session:${profileId}`) || "",
  );
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    api<Session[]>("sessions")
      .then((rows) => {
        setSessions(rows);
        if (!rows.some((s) => s.id === session)) setSession(rows[0]?.id || "");
      })
      .catch((e) => setError(e.message));
  }, []); // Initial session discovery only.
  useEffect(() => {
    sessionStorage.setItem(`dla-session:${profileId}`, session);
    setError("");
    if (session) {
      let active = true;
      api<Message[]>(`sessions/${session}`)
        .then((rows) => {
          if (active) setMessages(rows);
        })
        .catch((e) => setError(e.message));
      return () => {
        active = false;
      };
    }
    setMessages([]);
  }, [session]);
  useEffect(() => {
    if (prompt) {
      setText(prompt);
      clearPrompt();
      composer.current?.focus();
    }
  }, [prompt, clearPrompt]);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);
  async function send() {
    if (!text.trim() || busy) return;
    const question = text.trim();
    setText("");
    setBusy(true);
    setError("");
    try {
      let id = session;
      if (!id) {
        const created = await api<Session>("sessions", {});
        id = created.id;
        setSession(id);
      }
      setMessages((old) => [
        ...old,
        { id: -Date.now(), role: "user", payload: { message: question } },
      ]);
      await api<Reply>(`sessions/${id}/messages`, { message: question });
      setMessages(await api<Message[]>(`sessions/${id}`));
      setSessions(await api<Session[]>("sessions"));
    } catch (e) {
      setError((e as Error).message);
      setText(question);
    } finally {
      setBusy(false);
    }
  }
  async function save(message: Message) {
    try {
      await api("reviews", { session_id: session, message_id: message.id });
      onReview();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function remove() {
    if (session) {
      await api(`sessions/${session}`, undefined, "DELETE");
      setSessions(await api<Session[]>("sessions"));
      setSession("");
    }
  }
  return (
    <aside className="agent-panel">
      <div className="agent-heading">
        <div className="agent-mark">
          <Bot size={19} />
        </div>
        <div>
          <strong>DLA assistant</strong>
          <span>Ask • investigate • remember</span>
        </div>
        <button
          className="icon-button"
          aria-label="Close assistant"
          onClick={close}
        >
          <X size={18} />
        </button>
      </div>
      <div className="session-bar">
        <History size={14} />
        <select
          aria-label="Conversation"
          disabled={busy}
          value={session}
          onChange={(e) => setSession(e.target.value)}
        >
          <option value="">New conversation</option>
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title}
            </option>
          ))}
        </select>
        <button
          className="icon-button"
          title="New conversation"
          disabled={busy}
          onClick={() => setSession("")}
        >
          <Plus size={16} />
        </button>
        <button
          className="icon-button"
          title="Delete conversation"
          disabled={busy || !session}
          onClick={() => remove().catch((e) => setError(e.message))}
        >
          <Trash2 size={14} />
        </button>
      </div>
      <div className="agent-messages">
        {!messages.length && (
          <div className="agent-welcome">
            <span className="eyebrow">Your logistics copilot</span>
            <h2>
              Start with a question.
              <br />
              Follow the evidence.
            </h2>
            <p>
              Explore inventory, incoming supply, and shortage risk in plain
              language, and consult supply chain reference documents. Follow-up
              questions keep the conversation in context.
            </p>
            {suggestions.map((s) => (
              <button
                className="suggestion"
                key={s}
                onClick={() => {
                  setText(s);
                  composer.current?.focus();
                }}
              >
                {s}
                <span>↗</span>
              </button>
            ))}
            <div className="note">
              As of {catalog.as_of}. All operations are simulated; catalog
              identities are real.
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div className={`chat-message ${m.role}`} key={m.id}>
            <div className="message-label">
              {m.role === "user" ? "You" : "DLA assistant"}
              {m.payload.status === "error" && " · needs attention"}
            </div>
            <p>{m.payload.message}</p>
            {m.payload.result && (
              <Evidence
                result={m.payload.result}
                compact
                onSave={() => save(m)}
              />
            )}{" "}
            {m.payload.retrieval && (
              <DocumentEvidence
                evidence={m.payload.retrieval}
                onSave={!m.payload.result ? () => save(m) : undefined}
              />
            )}
            {m.role === "assistant" && m.payload.result && (
              <EmailAction reply={m.payload} />
            )}
            {m.payload.result && m.payload.as_of && (
              <span className="message-meta">
                Snapshot {m.payload.as_of} ·{" "}
                {m.payload.snapshot_id?.slice(0, 8)} · synthetic
              </span>
            )}
          </div>
        ))}
        {busy && (
          <div className="thinking">
            <LoaderCircle size={15} className="spin" /> Interpreting question
            and checking evidence…
          </div>
        )}
        <div ref={end} />
      </div>
      <div className="composer-area">
        {!catalog.llm_configured && (
          <div className="note warning">
            Connect a DataRobot LLM deployment to enable chat. Inventory and the
            SQL explorer are available now.
          </div>
        )}
        {catalog.retrieval && !catalog.retrieval.configured && (
          <div className="note">
            Document search is awaiting a vector database deployment.
            Operational questions are available.
          </div>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="composer">
          <textarea
            ref={composer}
            aria-label="Ask DLA assistant"
            placeholder="Ask about stock, orders, risk, or guidance…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={3}
            disabled={busy}
          />
          <button
            className="send-button"
            aria-label="Send question"
            disabled={busy || !text.trim() || !catalog.llm_configured}
            onClick={send}
          >
            <ArrowUp size={19} />
          </button>
        </div>
        <span className="composer-hint">
          Read-only analysis · inspect evidence before acting
        </span>
      </div>
    </aside>
  );
}
