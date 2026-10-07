import type { Reply, Row } from "./api";

export function orderRows(reply: Reply): Row[] {
  const seen = new Set<string>();
  return (reply.result?.rows || []).filter((row) => {
    const id = String(row.order_id || "");
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}
const field = (value: unknown) =>
  String(value ?? "Not provided")
    .replace(/[\r\n\t]/g, " ")
    .slice(0, 200);
export function draftEmail(rows: Row[], asOf?: string) {
  if (!rows.length || rows.length > 3)
    throw new Error("Choose one to three orders.");
  return {
    subject: `Status follow-up: ${rows.map((row) => field(row.order_id)).join(", ")}`,
    body: `DEMO — SYNTHETIC OPERATIONS\n\nHello,\n\nPlease help verify the current status of the following orders in our simulated snapshot${asOf ? ` dated ${field(asOf)}` : ""}:\n\n${rows
      .map((row) =>
        [
          `Order ${field(row.order_id)} — ${field(row.item_name)}`,
          `NIIN: ${field(row.niin)} | Facility: ${field(row.destination ?? row.location).replace("SYNTHETIC_DEPOT_", "Depot ")}`,
          `Quantity: ${field(row.quantity)} | Expected receipt: ${field(row.expected_receipt_date)}`,
        ].join("\n"),
      )
      .join(
        "\n\n",
      )}\n\nCould you confirm whether receipt has been recorded, provide any available shipment status and updated expected receipt date, and identify any issue requiring review?\n\nThis is an analyst-drafted status inquiry, not a formal MILSTRIP transaction or authorization to change an order.\n\nThank you,`,
  };
}
export function suggestedRecipient(question = "") {
  return (
    question.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/)?.[0] || ""
  );
}
export function mailtoDraft(to: string, subject: string, body: string) {
  if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(to.trim()))
    throw new Error("Enter one valid recipient email address.");
  if (/[\r\n]/.test(subject)) throw new Error("Subject must be one line.");
  const href = `mailto:${encodeURIComponent(to.trim())}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  if (href.length > 7000)
    throw new Error(
      "This draft is too long for an email link. Use Copy draft instead.",
    );
  return href;
}
