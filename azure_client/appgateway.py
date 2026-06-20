"""
Application Gateway and WAF data via Resource Graph.
Read-only — only fetches configuration and status.
"""

import json
from azure_client.network import _query


def fetch_app_gateways() -> list[dict]:
    kql = """
    Resources
    | where type == 'microsoft.network/applicationgateways'
    | project
        gw_id           = id,
        name,
        resource_group  = resourceGroup,
        subscription_id = subscriptionId,
        location,
        sku_name        = tostring(properties.sku.name),
        sku_tier        = tostring(properties.sku.tier),
        operational_state = tostring(properties.operationalState),
        capacity        = toint(properties.sku.capacity),
        waf_enabled     = tobool(properties.webApplicationFirewallConfiguration.enabled),
        waf_mode        = tostring(properties.webApplicationFirewallConfiguration.firewallMode),
        owasp_version   = tostring(properties.webApplicationFirewallConfiguration.ruleSetVersion),
        frontend_ips    = tostring(properties.frontendIPConfigurations),
        tags            = tostring(tags)
    | order by name asc
    """
    try:
        rows = _query(kql)
    except Exception as exc:
        return [{"_error": str(exc)}]

    result = []
    for r in rows:
        gw_id = r.get("gw_id", "")
        if not gw_id:
            continue

        # Parse frontend IPs from JSON
        frontend_raw = r.get("frontend_ips") or "[]"
        try:
            fe_data = json.loads(frontend_raw) if isinstance(frontend_raw, str) else frontend_raw
            ips = []
            if isinstance(fe_data, list):
                for fe in fe_data:
                    pip_ref = (fe.get("properties") or {}).get("publicIPAddress") or {}
                    priv = (fe.get("properties") or {}).get("privateIPAddress")
                    if priv:
                        ips.append(priv)
                    if pip_ref.get("id"):
                        ips.append(pip_ref["id"].split("/")[-1])
            frontend_str = ", ".join(ips) if ips else ""
        except Exception:
            frontend_str = ""

        result.append({
            "gw_id": gw_id,
            "name": r.get("name", ""),
            "resource_group": r.get("resource_group", ""),
            "subscription_id": r.get("subscription_id", ""),
            "location": r.get("location", ""),
            "sku_name": r.get("sku_name", ""),
            "sku_tier": r.get("sku_tier", ""),
            "operational_state": r.get("operational_state", ""),
            "capacity": r.get("capacity"),
            "waf_enabled": 1 if r.get("waf_enabled") else 0,
            "waf_mode": r.get("waf_mode") or "",
            "owasp_version": r.get("owasp_version") or "",
            "frontend_ips": frontend_str,
            "tags": str(r.get("tags") or "{}"),
        })
    return result


def fetch_waf_rules_from_gateways() -> list[dict]:
    """Fetch WAF managed rule groups from Application Gateways."""
    kql = """
    Resources
    | where type == 'microsoft.network/applicationgateways'
    | where isnotnull(properties.webApplicationFirewallConfiguration)
    | mv-expand rg = properties.webApplicationFirewallConfiguration.disabledRuleGroups
    | project
        gw_id    = id,
        gw_name  = name,
        rule_set_type    = tostring(properties.webApplicationFirewallConfiguration.ruleSetType),
        rule_set_version = tostring(properties.webApplicationFirewallConfiguration.ruleSetVersion),
        rule_group       = tostring(rg.ruleGroupName),
        state            = 'Disabled'
    | order by gw_name asc, rule_group asc
    """
    try:
        rows = _query(kql)
    except Exception as exc:
        return [{"_error": str(exc)}]

    result = []
    for r in rows:
        gw_id = r.get("gw_id", "")
        if not gw_id:
            continue
        rg_name = r.get("rule_group", "")
        result.append({
            "rule_id": f"{gw_id}/{rg_name}",
            "gw_id": gw_id,
            "gw_name": r.get("gw_name", ""),
            "rule_set_type": r.get("rule_set_type", ""),
            "rule_set_version": r.get("rule_set_version", ""),
            "rule_group": rg_name,
            "rule_rule_id": "",
            "state": "Disabled",
            "action": "Disabled",
        })
    return result


def fetch_all_appgateways() -> dict:
    return {
        "gateways": fetch_app_gateways(),
        "waf_rules": fetch_waf_rules_from_gateways(),
    }
