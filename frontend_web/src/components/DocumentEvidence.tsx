import type { Retrieval } from "../api";

export function DocumentEvidence({
  evidence,
  onSave,
}: {
  evidence: Retrieval;
  onSave?: () => void;
}) {
  const documents = evidence.documents || [];
  return (
    <section className="document-evidence" aria-label="Document evidence">
      <div className="evidence-heading">
        <strong>Document evidence · {documents.length} passages</strong>
        {onSave && documents.length > 0 && (
          <button className="text-button" onClick={onSave}>
            Save for review
          </button>
        )}
      </div>
      <p className="message-meta">{evidence.name} · reference guidance</p>
      {evidence.error && <p className="note warning">{evidence.error}</p>}
      {evidence.status === "empty" && (
        <p className="note">No usable passages returned.</p>
      )}
      {documents.map((document) => (
        <details key={document.id} className="document-passage">
          <summary>
            <strong>[{document.id}]</strong> {document.source}
          </summary>
          {Object.keys(document.metadata).length > 0 && (
            <dl>
              {Object.entries(document.metadata).map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          )}
          <blockquote>{document.text}</blockquote>
          {document.truncated && (
            <span className="message-meta">
              Excerpt shortened for display and model context.
            </span>
          )}
        </details>
      ))}
      <details className="document-provenance">
        <summary>Retrieval details</summary>
        <p>Search: {evidence.query}</p>
        <p>Vector database: {evidence.vector_database_id}</p>
        <p>Deployment: {evidence.deployment_id}</p>
        {evidence.model_id && <p>Model version ID: {evidence.model_id}</p>}
        {evidence.retrieved_at && <p>Retrieved: {evidence.retrieved_at}</p>}
        <p>
          Passages are search matches, not automatic approval to act. Check
          document applicability and revision dates.
        </p>
      </details>
    </section>
  );
}
