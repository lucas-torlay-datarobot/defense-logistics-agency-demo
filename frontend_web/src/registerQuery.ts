export type RegisterKind = "overview" | "inventory" | "orders";
export const PAGE_SIZE = 50;
export const defaults = {
  search: "",
  location: "",
  status: "all",
  due: "all",
  minQuantity: "",
  maxQuantity: "",
  minRisk: "",
  maxRisk: "",
  stock: "all",
  maxCover: "",
  dateFrom: "",
  dateTo: "",
  sort: "",
  direction: "desc",
};
export type Filters = typeof defaults;
export const sortOptions = (kind: RegisterKind) =>
  kind === "orders"
    ? {
        order_date: "Order date",
        expected_receipt_date: "Expected receipt date",
        actual_receipt_date: "Actual receipt date",
        quantity: "Order quantity",
        shortage_probability_14d: "Current shortage risk",
        item_name: "Item name",
        niin: "NIIN",
      }
    : {
        shortage_probability_14d: "Shortage risk",
        closing_stock: "Closing stock",
        days_of_cover: "Days of cover",
        item_name: "Item name",
        niin: "NIIN",
      };
const quote = (value: string) => "'" + value.replaceAll("'", "''") + "'";
export function registerQuery(
  kind: RegisterKind,
  f: Filters,
  asOf: string,
  page: number,
) {
  const orders = kind === "orders";
  const conditions: string[] = [];
  const range = (field: string, value: string, op: string, percent = false) => {
    if (!value.trim()) return;
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0 || (percent && n > 100))
      throw new Error(
        "Enter nonnegative numbers; risk must be between 0 and 100%.",
      );
    conditions.push(`${field} ${op} ${percent ? n / 100 : n}`);
  };
  const checkRange = (lo: string, hi: string) => {
    if (lo !== "" && hi !== "" && Number(lo) > Number(hi))
      throw new Error("Minimum must not exceed maximum.");
  };
  checkRange(f.minRisk, f.maxRisk);
  if (f.search.trim()) {
    const text = quote(f.search.trim());
    conditions.push(
      `(contains(lower(item_name), lower(${text})) OR contains(niin, ${text}) OR contains(nsn, ${text})${orders ? ` OR contains(lower(order_id), lower(${text}))` : ""})`,
    );
  }
  if (f.location)
    conditions.push(
      `${orders ? "destination" : "location"} = ${quote(f.location)}`,
    );
  range("shortage_probability_14d", f.minRisk, ">=", true);
  range("shortage_probability_14d", f.maxRisk, "<=", true);
  if (orders) {
    checkRange(f.minQuantity, f.maxQuantity);
    range("quantity", f.minQuantity, ">=");
    range("quantity", f.maxQuantity, "<=");
    if (f.status === "open") conditions.push("is_open");
    if (f.status === "received") conditions.push("NOT is_open");
    if (f.due === "overdue") conditions.push("is_overdue");
    if (f.due === "future")
      conditions.push(
        `is_open AND expected_receipt_date > CAST(${quote(asOf)} AS DATE)`,
      );
    if (f.dateFrom && f.dateTo && f.dateFrom > f.dateTo)
      throw new Error("Expected receipt start date must not exceed end date.");
    for (const [value, op] of [
      [f.dateFrom, ">="],
      [f.dateTo, "<="],
    ]) {
      if (value) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(value))
          throw new Error("Choose a valid receipt date.");
        conditions.push(
          `expected_receipt_date ${op} CAST(${quote(value)} AS DATE)`,
        );
      }
    }
  } else {
    if (f.stock === "zero") conditions.push("closing_stock = 0");
    if (f.stock === "positive") conditions.push("closing_stock > 0");
    if (f.stock === "unscored")
      conditions.push("shortage_probability_14d IS NULL");
    range("days_of_cover", f.maxCover, "<=");
  }
  const allowed = sortOptions(kind);
  const sort =
    f.sort && f.sort in allowed
      ? f.sort
      : orders
        ? "order_date"
        : "shortage_probability_14d";
  const direction = f.direction === "asc" ? "ASC" : "DESC";
  const source = orders
    ? `WITH register AS (
    SELECT o.order_id, o.niin, i.nsn, i.item_name, i.fsc, o.destination,
      o.quantity, o.order_date, o.expected_receipt_date, o.actual_receipt_date,
      CASE WHEN NOT o.is_open THEN 'Received' WHEN o.is_overdue THEN 'Overdue' ELSE 'Open — future due' END AS order_status,
      o.is_open, o.is_overdue, l.closing_stock, l.days_of_cover, l.shortage_probability_14d
    FROM replenishment_orders o LEFT JOIN items i ON o.niin = i.niin
    LEFT JOIN latest_inventory l ON o.niin = l.niin AND o.destination = l.location
  )`
    : "";
  const columns = orders
    ? "order_id, niin, item_name, nsn, fsc, destination, quantity, order_status, order_date, expected_receipt_date, actual_receipt_date, closing_stock, days_of_cover, shortage_probability_14d"
    : "niin, item_name, nsn, fsc, location, closing_stock, days_of_cover, shortage_probability_14d";
  return `${source} SELECT ${columns} FROM ${orders ? "register" : "latest_inventory"}
    ${conditions.length ? "WHERE " + conditions.join(" AND ") : ""}
    ORDER BY ${sort} ${direction} NULLS LAST, ${orders ? "order_id" : "niin, location"}
    LIMIT ${PAGE_SIZE + 1} OFFSET ${Math.max(0, Math.floor(page)) * PAGE_SIZE}`;
}
