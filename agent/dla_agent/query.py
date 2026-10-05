"""Bounded read-only SQL over a curated, local snapshot; no arbitrary data access."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import math
import threading
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

TABLES = {
    "daily_inventory": "One row per date, NIIN, location. Simulated observed inventory and demand.",
    "replenishment_orders": "One row per order_id. Simulated orders; destination joins inventory.location. NULL actual_receipt_date means not observed received, NOT zero lead time. is_overdue means still open after the expected receipt date has ended (expected_receipt_date <= snapshot date).",
    "items": "One row per NIIN. Real catalog identifiers/names carried into the synthetic scenario; only this scenario's subset, not the full FLIS catalog.",
    "latest_inventory": "One row per NIIN, location, at the snapshot date. End-of-day stock, trailing demand, coverage, open orders; probability is NULL unless a model score exists.",
    "risk_scores": "One row per date, NIIN, location. DataRobot probability of ANY unfulfilled demand in the NEXT 14 days; not a forecast of shortage quantity or a causal effect.",
}
FUNCTIONS = set(
    """AND OR ABS AVG CEIL CEILING FLOOR ROUND SUM COUNT MIN MAX COALESCE NULLIF
    CAST TRY_CAST UPPER LOWER TRIM LTRIM RTRIM LENGTH CHAR_LENGTH SUBSTRING CONCAT
    CONCAT_WS REPLACE LIKE ILIKE STARTS_WITH ENDS_WITH CONTAINS
    DATE DATE_TRUNC TIMESTAMP_TRUNC DATE_DIFF DATEDIFF DATE_ADD DATE_SUB
    EXTRACT YEAR MONTH DAY STRFTIME TIME_TO_STR IF CASE
    ROW_NUMBER RANK DENSE_RANK LAG LEAD FIRST_VALUE LAST_VALUE
    STDDEV STDDEV_SAMP STDDEV_POP VARIANCE VAR_SAMP VAR_POP MEDIAN QUANTILE_CONT
    BOOL_OR BOOL_AND GREATEST LEAST PERCENTILE_CONT POWER SQRT
""".split()
)


def json_value(value):
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        return value[:2000]
    return value


def validate_sql(sql):
    if len(sql) > 16000:
        raise ValueError("Query is too long")
    statements = sqlglot.parse(sql, read="duckdb")
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
        raise ValueError("Only one SELECT or non-recursive WITH query is allowed")
    tree = statements[0]
    forbidden = (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock)
    for node in tree.walk():
        # SELECT * is permitted. Star modifiers cannot inject statements.
        if isinstance(node, forbidden):
            raise ValueError("Read-only queries only")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise ValueError("Recursive queries are not supported")
        if isinstance(node, exp.Func):
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            if name.upper() not in FUNCTIONS:
                raise ValueError(f"Function is not available: {name}")
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    sources = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not isinstance(table.this, exp.Identifier) or table.db or table.catalog:
            raise ValueError("External sources, schemas and table functions are not available")
        if name not in TABLES and name not in ctes:
            raise ValueError(f"Table is not available: {name}")
        if name in TABLES:
            sources.add(name)
    if not sources:
        raise ValueError("Query must reference the available scenario tables")
    return tree.sql(dialect="duckdb"), sorted(sources)


class QueryService:
    def __init__(self, path: Path, timeout=12, max_rows=200):
        self.path = Path(path)
        self.timeout = timeout
        self.max_rows = max_rows
        stat = self.path.stat()
        self.identity = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        # One analytical query at a time keeps memory bounded in a Codespace.
        self.lock = threading.Lock()

    def connect(self):
        stat = self.path.stat()
        if self.identity != (stat.st_ino, stat.st_size, stat.st_mtime_ns):
            raise RuntimeError("Snapshot changed; restart the app to load it consistently")
        return duckdb.connect(
            str(self.path),
            read_only=True,
            config={
                "enable_external_access": "false",
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
                "allow_community_extensions": "false",
                "threads": "1",
                "memory_limit": "512MB",
                "max_temp_directory_size": "0B",
                "lock_configuration": "true",
            },
        )

    def metadata(self):
        with self.lock, self.connect() as con:
            manifest = json.loads(con.execute("SELECT payload FROM app_metadata").fetchone()[0])
            schema = {
                table: {
                    "description": description,
                    "columns": [
                        {"name": row[0], "type": row[1]}
                        for row in con.execute(f'DESCRIBE "{table}"').fetchall()
                    ],
                }
                for table, description in TABLES.items()
            }
        return {**manifest, "tables": schema}

    def execute(self, sql, *, max_rows=None):
        sql, sources = validate_sql(sql)
        limit = min(max_rows or self.max_rows, self.max_rows)
        with self.lock, self.connect() as con:
            timer = threading.Timer(self.timeout, con.interrupt)
            timer.daemon = True
            timer.start()
            try:
                cursor = con.execute(f"SELECT * FROM ({sql}) AS bounded_result LIMIT {limit + 1}")
                columns = [c[0] for c in cursor.description]
                if len(columns) > 60 or len(set(columns)) != len(columns):
                    raise ValueError("Select at most 60 columns, with unique aliases")
                values = cursor.fetchall()
                rows = [
                    dict(zip(columns, map(json_value, row), strict=True)) for row in values[:limit]
                ]
                # Bound result bytes as well as row count; the agent never receives an unlimited dump.
                while len(json.dumps(rows, default=str)) > 100000:
                    rows.pop()
                truncated = len(values) > len(rows)
            finally:
                timer.cancel()
                timer.join()
        return {
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "sources": sources,
            "truncated": truncated,
            "row_limit": limit,
            "is_synthetic": True,
        }
