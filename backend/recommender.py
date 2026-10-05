"""
recommender.py
--------------
Recommendation engine.

Maps root cause pattern names → ordered list of recommended actions.
The mapping is a simple dictionary – easy to extend, load from config,
or replace with a retrieval-augmented generation (RAG) call in the future.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import DeviceStatus, Recommendation, RootCauseResult


# ---------------------------------------------------------------------------
# Recommendation library
# ---------------------------------------------------------------------------
# Key = failure_type (matches pattern names in root_cause.py)
# Value = ordered list of recommended action strings

RECOMMENDATIONS: Dict[str, List[str]] = {
    "SESSION_ESTABLISHMENT": [
        "Check session establishment configuration on the server side",
        "Review server-side resource limits (file descriptors, connection pool size)",
        "Inspect component logs around the failure timestamp for stack traces",
        "Verify that the authentication token has not expired before session creation",
        "Investigate repeated session establishment failures for patterns (time-of-day, load)",
    ],
    "AUTHENTICATION_FAILURE": [
        "Verify credentials, API keys, or certificates used by the device",
        "Check the identity provider / authentication service availability",
        "Review certificate expiry dates",
        "Rotate credentials if a breach is suspected",
        "Enable detailed authentication logging on the server for more context",
    ],
    "CONNECTION_FAILURE": [
        "Verify network connectivity from the device to the target host",
        "Check firewall rules and security group configurations",
        "Confirm the target host is running and accepting connections",
        "Review DNS resolution for the target hostname",
        "Check for ISP or VPN issues affecting the network path",
    ],
    "DATA_TRANSFER_FAILURE": [
        "Check for network stability issues (packet loss, jitter)",
        "Review transfer timeout settings on both client and server",
        "Inspect payload size limits and compression settings",
        "Look for errors in the receiving service's application logs",
        "Consider implementing resumable transfer / checkpointing",
    ],
    "EXCESSIVE_RETRIES": [
        "Implement exponential back-off with jitter to reduce retry storms",
        "Investigate intermittent connectivity to the backend service",
        "Set a maximum retry limit and alert after it is breached",
        "Review health check configuration to detect failures faster",
        "Check whether the target service is being rate-limited or throttled",
    ],
    "MISSING_EVENTS": [
        "Review application logs for crashes or early exits before the missing events",
        "Check exception handling — the application may be swallowing errors silently",
        "Inspect OS-level logs (dmesg, syslog) for OOM kills or signal terminations",
        "Verify that all lifecycle stages emit events (instrumentation coverage)",
        "Consider adding health-beat / heartbeat events for long-running steps",
    ],
    "LONG_DELAY": [
        "Profile the slow operation to identify the bottleneck",
        "Check downstream dependencies (databases, queues, external APIs) for latency",
        "Review timeout configuration — timeouts may be set too high",
        "Add distributed tracing to pinpoint where time is spent",
        "Consider caching or pre-fetching data to reduce blocking wait times",
    ],
    "UNEXPECTED_SEQUENCE": [
        "Enable detailed event tracing and correlate with application code paths",
        "Review concurrency controls — a race condition may be causing out-of-order events",
        "Check whether event timestamps are accurate (clock skew between nodes)",
        "Audit recent code changes that may have altered the event emission order",
        "Add state-machine assertions to enforce the expected lifecycle sequence",
    ],
    "UNKNOWN": [
        "Collect and review full device logs for the affected time window",
        "Escalate to the on-call engineering team for manual investigation",
        "Increase log verbosity on the device for the next occurrence",
        "Correlate this failure with other devices to identify a systemic issue",
    ],
}

_HEALTHY_NOTE = ["Device is operating normally — no action required."]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_recommendation(rca: RootCauseResult) -> Recommendation:
    """
    Return a :class:`Recommendation` for the given root cause result.
    """
    if rca.status == DeviceStatus.HEALTHY:
        return Recommendation(device_id=rca.device_id, recommendation=_HEALTHY_NOTE)

    actions = RECOMMENDATIONS.get(rca.failure_type or "UNKNOWN", RECOMMENDATIONS["UNKNOWN"])

    return Recommendation(device_id=rca.device_id, recommendation=actions)


def generate_all_recommendations(
    rca_results: Dict[str, RootCauseResult],
) -> Dict[str, Recommendation]:
    """Generate recommendations for every device."""
    return {
        device_id: generate_recommendation(rca)
        for device_id, rca in rca_results.items()
    }
