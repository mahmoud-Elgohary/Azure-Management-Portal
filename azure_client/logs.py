"""
Log Analytics KQL execution — live on demand, never cached.
Used only by the KQL console route; never called during sync.
"""

from datetime import timedelta

from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from azure.core.exceptions import HttpResponseError

from azure_client.auth import get_credential


def _client() -> LogsQueryClient:
    return LogsQueryClient(get_credential())


def run_kql(workspace_id: str, query: str, timeout_seconds: int = 30, max_rows: int = 1000) -> dict:
    """
    Execute a KQL query against a Log Analytics workspace.
    Returns {columns, rows, row_count, truncated, elapsed_ms} on success,
    or {error, log_coverage_hint} when data is absent or permission is missing.
    """
    import time
    t0 = time.monotonic()

    safe_query = f"{query}\n| limit {max_rows}"

    try:
        client = _client()
        response = client.query_workspace(
            workspace_id=workspace_id,
            query=safe_query,
            timespan=timedelta(days=1),
            server_timeout=timeout_seconds,
        )
    except HttpResponseError as exc:
        msg = str(exc)
        if "403" in msg or "Forbidden" in msg or "AuthorizationFailed" in msg:
            return {
                "error": "Permission denied. The data SP needs 'Log Analytics Data Reader' on the workspace.",
                "detail": msg,
            }
        return {"error": f"Azure API error: {msg}"}
    except Exception as exc:
        return {"error": str(exc)}

    elapsed_ms = round((time.monotonic() - t0) * 1000)

    if response.status == LogsQueryStatus.FAILURE:
        return {"error": str(response.partial_error or "Query failed")}

    table = response.tables[0] if response.tables else None
    if table is None:
        return {
            "columns": [], "rows": [], "row_count": 0, "truncated": False, "elapsed_ms": elapsed_ms,
            "no_data_hint": "Query returned no tables. Verify the workspace ID and that diagnostic settings / AMA are shipping logs here.",
        }

    columns = list(table.columns)  # azure-monitor-query ≥1.3 returns List[str] directly
    raw_rows = table.rows
    truncated = len(raw_rows) >= max_rows

    # Serialise rows to JSON-safe types
    def _safe(v):
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return v

    rows = [[_safe(cell) for cell in row] for row in raw_rows]

    result = {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
    }

    if not rows:
        result["no_data_hint"] = (
            "Query ran successfully but returned 0 rows. "
            "This resource may not be shipping logs to this workspace yet. "
            "Enable via: Azure Portal → resource → Diagnostic settings → Add setting → send to Log Analytics."
        )

    return result
