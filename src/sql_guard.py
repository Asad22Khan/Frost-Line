"""Application-level SQL firewall for agent-written queries.

The database role is the real security boundary (see sql/security_layer.sql). This guard is the second
layer: it parses the SQL the LLM wrote and rejects anything that isn't a single read-only query against
the approved view, then caps the number of rows. Rejections come back as plain-English reasons so the
agent can correct its own query.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

ALLOWED_TABLES = {"vw_active_fleet"}
ALLOWED_SCHEMAS = {"", "fde_views"}
MAX_ROWS = 50

# Functions that touch the server, files, sessions or timing. They have no place in analytics queries.
BLOCKED_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "set_config", "current_setting", "query_to_xml", "copy")


class UnsafeQueryError(ValueError):
    """Raised when a query is rejected. The message is safe to show to the agent."""


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return (node.sql_name() or "").lower()


def validate_and_limit(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Return a safe, row-limited version of `sql`, or raise UnsafeQueryError explaining why it was rejected."""
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as exc:
        raise UnsafeQueryError(f"Could not parse the SQL: {exc}") from None

    if len(statements) != 1:
        raise UnsafeQueryError("Exactly one SQL statement is allowed.")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise UnsafeQueryError(f"Only SELECT queries are allowed (got {tree.key.upper()}).")

    for node in tree.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
                             exp.Alter, exp.Command, exp.Into, exp.Lock)):
            raise UnsafeQueryError("Only read-only SELECT queries are allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name, schema = table.name.lower(), (table.db or "").lower()
        if name in cte_names and not schema:
            continue
        if name not in ALLOWED_TABLES or schema not in ALLOWED_SCHEMAS:
            raise UnsafeQueryError(f"Table '{table.sql()}' is not available. Query fde_views.vw_active_fleet only.")

    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        if name.startswith(BLOCKED_FUNCTION_PREFIXES):
            raise UnsafeQueryError(f"Function '{name}' is not allowed.")

    # Enforce a row cap on the outermost query: add LIMIT if missing, lower it if too high.
    limit = tree.args.get("limit")
    current = None
    if limit is not None:
        value = limit.args.get("expression") or limit.this
        current = int(value.name) if isinstance(value, exp.Literal) and value.is_int else None
    if current is None or current > max_rows:
        tree = tree.limit(max_rows) if isinstance(tree, exp.Select) else exp.select("*").from_(
            tree.subquery("q")).limit(max_rows)

    return tree.sql(dialect="postgres")
