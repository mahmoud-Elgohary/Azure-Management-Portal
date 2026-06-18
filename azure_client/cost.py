"""
Cost Management — MTD spend, daily trend, anomaly detection.
"""

from datetime import date, timedelta

from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    QueryDefinition,
    QueryTimePeriod,
    QueryDataset,
    QueryAggregation,
    QueryGrouping,
)

from azure_client.auth import get_credential, subscription_ids
import config


def _client() -> CostManagementClient:
    return CostManagementClient(get_credential())


def _scope(sub_id: str) -> str:
    return f"/subscriptions/{sub_id}"


def fetch_mtd_cost(sub_id: str) -> list[dict]:
    """Daily cost rows for the current month so far."""
    client = _client()
    today = date.today()
    first_of_month = today.replace(day=1)

    query = QueryDefinition(
        type="ActualCost",
        timeframe="Custom",
        time_period=QueryTimePeriod(
            from_property=first_of_month.strftime("%Y-%m-%dT00:00:00Z"),
            to=today.strftime("%Y-%m-%dT23:59:59Z"),
        ),
        dataset=QueryDataset(
            granularity="Daily",
            aggregation={"totalCost": QueryAggregation(name="PreTaxCost", function="Sum")},
            grouping=[QueryGrouping(type="Dimension", name="ResourceGroupName")],
        ),
    )

    try:
        result = client.query.usage(_scope(sub_id), query)
        rows = []
        if result.rows:
            cols = [c.name for c in result.columns]
            for row in result.rows:
                d = dict(zip(cols, row))
                rows.append(
                    {
                        "subscription_id": sub_id,
                        "date": str(d.get("UsageDate", "")),
                        "resource_group": str(d.get("ResourceGroupName", "")),
                        "cost": float(d.get("PreTaxCost", 0)),
                        "currency": str(d.get("Currency", "USD")),
                    }
                )
        return rows
    except Exception as exc:
        return [{"_error": str(exc), "subscription_id": sub_id}]


def detect_anomalies(daily_rows: list[dict], lookback_days: int = 7) -> list[dict]:
    """
    Flag any day where cost exceeds COST_ANOMALY_MULTIPLIER × recent average.
    Returns list of anomalous row dicts with an added 'anomaly' key.
    """
    from collections import defaultdict

    by_date: dict[str, float] = defaultdict(float)
    for r in daily_rows:
        if "_error" not in r:
            by_date[r["date"]] += r["cost"]

    sorted_dates = sorted(by_date.keys())
    anomalies = []
    for i, d in enumerate(sorted_dates):
        recent = sorted_dates[max(0, i - lookback_days) : i]
        if not recent:
            continue
        avg = sum(by_date[x] for x in recent) / len(recent)
        if avg > 0 and by_date[d] > avg * config.COST_ANOMALY_MULTIPLIER:
            anomalies.append({"date": d, "cost": by_date[d], "avg": avg, "anomaly": True})
    return anomalies


def fetch_all_costs() -> list[dict]:
    rows = []
    for sub in subscription_ids():
        rows.extend(fetch_mtd_cost(sub))
    return rows
