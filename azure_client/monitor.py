"""
Azure Monitor metrics for VMs — CPU, memory, disk, network.
Stores time-series data for trend charts.
"""

from datetime import datetime, timedelta, timezone

from azure.monitor.query import MetricsQueryClient, MetricAggregationType

from azure_client.auth import get_credential


_METRICS = [
    "Percentage CPU",
    "Available Memory Bytes",
    "Disk Read Bytes",
    "Disk Write Bytes",
    "Network In Total",
    "Network Out Total",
]


def _client() -> MetricsQueryClient:
    return MetricsQueryClient(get_credential())


def fetch_vm_metrics(vm_id: str, hours: int = 1) -> list[dict]:
    """
    Query the last `hours` of metrics for a single VM resource ID.
    Returns a flat list of {'metric', 'timestamp', 'value'} dicts.
    """
    client = _client()
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    try:
        result = client.query_resource(
            resource_uri=vm_id,
            metric_names=_METRICS,
            timespan=(start, end),
            granularity=timedelta(minutes=5),
            aggregations=[MetricAggregationType.AVERAGE],
        )
    except Exception as exc:
        return [{"_error": str(exc), "vm_id": vm_id}]

    rows = []
    for metric in result.metrics:
        for ts in metric.timeseries:
            for point in ts.data:
                if point.average is not None:
                    rows.append(
                        {
                            "vm_id": vm_id,
                            "metric": metric.name,
                            "timestamp": point.timestamp.isoformat(),
                            "value": point.average,
                        }
                    )
    return rows


def latest_cpu_pct(vm_id: str) -> float | None:
    rows = fetch_vm_metrics(vm_id, hours=1)
    cpu_rows = [r for r in rows if r.get("metric") == "Percentage CPU"]
    if not cpu_rows:
        return None
    return cpu_rows[-1]["value"]
