"""
Azure DevOps REST API client — read-only.
Requires AZURE_DEVOPS_ORG and AZURE_DEVOPS_PAT in .env.
All functions return plain dicts; no DB writes here.
"""

import base64
import logging
import requests

import config

log = logging.getLogger(__name__)

_API = "https://dev.azure.com"
_API_VERSION = "7.1"


def _enabled() -> bool:
    return bool(config.AZURE_DEVOPS_ORG and config.AZURE_DEVOPS_PAT)


def _session() -> requests.Session:
    s = requests.Session()
    token = base64.b64encode(f":{config.AZURE_DEVOPS_PAT}".encode()).decode()
    s.headers.update({
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    })
    return s


def _get(path: str, params: dict = None) -> dict:
    org = config.AZURE_DEVOPS_ORG
    url = f"{_API}/{org}/{path}"
    p = {"api-version": _API_VERSION}
    if params:
        p.update(params)
    r = _session().get(url, params=p, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_projects() -> list[dict]:
    if not _enabled():
        return []
    data = _get("_apis/projects", {"$top": 100, "stateFilter": "All"})
    result = []
    for p in data.get("value", []):
        result.append({
            "project_id":   p.get("id", ""),
            "name":         p.get("name", ""),
            "description":  p.get("description", ""),
            "state":        p.get("state", ""),
            "visibility":   p.get("visibility", ""),
            "last_update":  p.get("lastUpdateTime", ""),
        })
    return result


def fetch_pipelines(project_id: str, project_name: str) -> list[dict]:
    """Fetch build definitions (CI/CD pipelines) for a project."""
    if not _enabled():
        return []
    data = _get(f"{project_name}/_apis/build/definitions", {
        "$top": 200,
        "includeLatestBuilds": "true",
    })
    result = []
    for d in data.get("value", []):
        lb = (d.get("latestBuild") or d.get("latestCompletedBuild") or {})
        result.append({
            "pipeline_id":       f"{project_id}/{d.get('id')}",
            "definition_id":     d.get("id"),
            "project_id":        project_id,
            "project_name":      project_name,
            "name":              d.get("name", ""),
            "folder":            (d.get("path") or "\\").lstrip("\\") or "\\",
            "queue_status":      d.get("queueStatus", "enabled"),
            "last_build_id":     lb.get("id"),
            "last_build_number": lb.get("buildNumber"),
            "last_build_result": lb.get("result"),
            "last_build_time":   lb.get("finishTime") or lb.get("startTime"),
        })
    return result


def fetch_builds(project_id: str, project_name: str, max_builds: int = 50) -> list[dict]:
    """Fetch recent builds for a project."""
    if not _enabled():
        return []
    data = _get(f"{project_name}/_apis/build/builds", {
        "$top": max_builds,
        "statusFilter": "completed",
        "queryOrder": "finishTimeDescending",
    })
    result = []
    for b in data.get("value", []):
        start = b.get("startTime", "")
        finish = b.get("finishTime", "")
        duration = 0
        if start and finish:
            from datetime import datetime
            try:
                fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
                s = datetime.strptime(start[:26] + "Z" if len(start) > 19 else start, fmt)
                f = datetime.strptime(finish[:26] + "Z" if len(finish) > 19 else finish, fmt)
                duration = int((f - s).total_seconds())
            except Exception:
                pass
        result.append({
            "build_id":       f"{project_id}/{b.get('id')}",
            "ado_id":         b.get("id"),
            "project_id":     project_id,
            "project_name":   project_name,
            "pipeline_name":  (b.get("definition") or {}).get("name", ""),
            "build_number":   b.get("buildNumber", ""),
            "status":         b.get("status", ""),
            "result":         b.get("result", ""),
            "start_time":     start,
            "finish_time":    finish,
            "duration_secs":  duration,
            "requested_by":   (b.get("requestedBy") or {}).get("displayName", ""),
            "branch":         (b.get("sourceBranch") or "").replace("refs/heads/", ""),
        })
    return result


def fetch_repos(project_id: str, project_name: str) -> list[dict]:
    """Fetch Git repositories for a project."""
    if not _enabled():
        return []
    data = _get(f"{project_name}/_apis/git/repositories")
    result = []
    for r in data.get("value", []):
        result.append({
            "repo_id":        r.get("id", ""),
            "project_id":     project_id,
            "project_name":   project_name,
            "name":           r.get("name", ""),
            "default_branch": (r.get("defaultBranch") or "").replace("refs/heads/", ""),
            "size_bytes":     r.get("size", 0),
            "remote_url":     r.get("remoteUrl", ""),
        })
    return result
