import { useState } from "react";
import { Mail } from "lucide-react";
import type { Reply } from "../api";
import {
  draftEmail,
  mailtoDraft,
  orderRows,
  suggestedRecipient,
} from "../emailDraft";

export function EmailAction({ reply }: { reply: Reply }) {
  const rows = orderRows(reply);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [recipient, setRecipient] = useState(
    suggestedRecipient(reply.question),
  );
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState("");
  if (!rows.length) return null;
  function prepare() {
    const draft = draftEmail(
      rows.filter((row) => selected.includes(String(row.order_id))),
      reply.as_of,
    );
    setSubject(draft.subject);
    setBody(draft.body);
    setEditing(true);
    setNotice("");
  }
  return (
    <section className="email-action">
      <button
        className="text-button"
        onClick={() => {
          setOpen(!open);
          setNotice("");
        }}
      >
        <Mail size={14} />
        {open ? "Close email draft" : "Draft follow-up email"}
      </button>
      {open && (
        <div className="email-composer">
          <strong>Follow-up email · draft only</strong>
          <p className="small muted">
            Choose up to three orders. Enter the intended POC yourself; no
            facility contact directory is connected.
          </p>
          {!editing ? (
            <>
              <div className="email-order-choices">
                {rows.map((row) => {
                  const id = String(row.order_id);
                  return (
                    <label key={id}>
                      <input
                        type="checkbox"
                        checked={selected.includes(id)}
                        disabled={
                          selected.length >= 3 && !selected.includes(id)
                        }
                        onChange={(event) =>
                          setSelected(
                            event.target.checked
                              ? [...selected, id]
                              : selected.filter((value) => value !== id),
                          )
                        }
                      />
                      <span>
                        {id} · {String(row.item_name || row.niin || "Item")} ·{" "}
                        {String(
                          row.destination ||
                            row.location ||
                            "Facility not provided",
                        ).replace("SYNTHETIC_DEPOT_", "Depot ")}
                      </span>
                    </label>
                  );
                })}
              </div>
              <button
                className="button secondary"
                disabled={!selected.length}
                onClick={prepare}
              >
                Prepare draft ({selected.length})
              </button>
            </>
          ) : (
            <>
              <label>
                To
                <input
                  type="email"
                  aria-label="Follow-up recipient"
                  placeholder="johndoe@xyz.com"
                  value={recipient}
                  onChange={(e) => {
                    setRecipient(e.target.value);
                    setNotice("");
                  }}
                />
              </label>
              <label>
                Subject
                <input
                  aria-label="Follow-up subject"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  maxLength={300}
                />
              </label>
              <label>
                Message
                <textarea
                  aria-label="Follow-up message"
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={12}
                  maxLength={10000}
                />
              </label>
              <div className="email-buttons">
                <button
                  className="text-button"
                  onClick={() => {
                    setEditing(false);
                    setNotice("");
                  }}
                >
                  Change orders
                </button>
                <button
                  className="text-button"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(
                        `To: ${recipient}\nSubject: ${subject}\n\n${body}`,
                      );
                      setNotice("Draft copied. Nothing sent.");
                    } catch {
                      setNotice(
                        "Clipboard unavailable. Select and copy the draft text manually.",
                      );
                    }
                  }}
                >
                  Copy draft
                </button>
                <button
                  className="button secondary"
                  onClick={() => {
                    try {
                      const href = mailtoDraft(recipient, subject, body);
                      window.location.href = href;
                      setNotice(
                        "Email app requested. Review and send there; nothing was sent by this app.",
                      );
                    } catch (error) {
                      setNotice((error as Error).message);
                    }
                  }}
                >
                  Open email app
                </button>
              </div>
              <p className="small muted">
                Review the recipient and content before sending. This uses your
                device’s email app. Changing orders regenerates the draft. Draft
                edits are not saved to chat history.
              </p>
            </>
          )}
          {notice && (
            <p className="note" role="status">
              {notice}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
