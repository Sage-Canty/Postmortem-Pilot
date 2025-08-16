"""
collector.py — fetches CloudWatch logs and GitHub Actions deploy history
"""

import os
import boto3
import requests
from datetime import datetime, timedelta


def collect_logs(log_group: str, start_time: datetime, end_time: datetime) -> list[dict]:
    """
    Fetch log events from CloudWatch Logs for the given group and time range.
    Returns a list of {timestamp, message} dicts, sorted by time.
    Filters out health check noise automatically.
    """
    client = boto3.client("logs")

    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)

    # Extend window slightly before incident to catch pre-incident signals
    pre_window_ms = int((start_time - timedelta(minutes=10)).timestamp() * 1000)

    events = []
    kwargs = {
        "logGroupName": log_group,
        "startTime": pre_window_ms,
        "endTime": end_ms,
        "limit": 500,
    }

    try:
        while True:
            response = client.filter_log_events(**kwargs)
            for event in response.get("events", []):
                msg = event.get("message", "").strip()
                # Filter health check noise
                if any(noise in msg for noise in [
                    "ELB-HealthChecker",
                    "health check",
                    "GET /health",
                    "GET /ping",
                ]):
                    continue
                events.append({
                    "timestamp": datetime.fromtimestamp(
                        event["timestamp"] / 1000
                    ).isoformat(),
                    "message": msg,
                })
            next_token = response.get("nextToken")
            if not next_token:
                break
            kwargs["nextToken"] = next_token
    except client.exceptions.ResourceNotFoundException:
        print(f"   ⚠️  Log group '{log_group}' not found in CloudWatch")
    except Exception as e:
        print(f"   ⚠️  CloudWatch error: {e}")

    return sorted(events, key=lambda x: x["timestamp"])


def collect_deploys(repo: str, start_time: datetime) -> list[dict]:
    """
    Fetch GitHub Actions workflow runs for the repo in the 24h window
    before the incident start time.
    Returns list of {time, workflow, status, commit, author} dicts.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("   ⚠️  GITHUB_TOKEN not set, skipping deploy history")
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    # Look back 24h before incident
    since = (start_time - timedelta(hours=24)).isoformat() + "Z"
    url = f"https://api.github.com/repos/{repo}/actions/runs"
    params = {"created": f">={since}", "per_page": 50}

    deploys = []
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        runs = response.json().get("workflow_runs", [])

        for run in runs:
            deploys.append({
                "time": run.get("created_at", ""),
                "workflow": run.get("name", ""),
                "status": run.get("conclusion", run.get("status", "")),
                "commit": run.get("head_sha", "")[:8],
                "commit_message": run.get("head_commit", {}).get("message", "").split("\n")[0],
                "author": run.get("head_commit", {}).get("author", {}).get("name", ""),
                "url": run.get("html_url", ""),
            })
    except requests.exceptions.RequestException as e:
        print(f"   ⚠️  GitHub API error: {e}")

    return sorted(deploys, key=lambda x: x["time"])


def build_context(
    log_group: str,
    start_time: datetime,
    end_time: datetime,
    alert: str,
    logs: list[dict],
    deploys: list[dict],
) -> dict:
    """
    Assemble all collected data into a single context dict
    passed to the RCA generator.
    """
    duration_minutes = int((end_time - start_time).total_seconds() / 60)

    # Extract error-level logs for prominence
    error_logs = [
        e for e in logs
        if any(kw in e["message"].lower() for kw in [
            "error", "exception", "fatal", "critical",
            "oom", "killed", "failed", "timeout", "refused"
        ])
    ]

    return {
        "log_group": log_group,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_minutes": duration_minutes,
        "alert": alert,
        "total_log_events": len(logs),
        "error_log_count": len(error_logs),
        "error_logs": error_logs[:50],   # cap at 50 to stay within token budget
        "all_logs": logs[:200],          # cap at 200 for full log context
        "deploys": deploys,
        "recent_deploys": [d for d in deploys if d.get("status") in ["failure", "success"]],
    }
