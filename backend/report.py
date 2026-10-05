"""
report.py
---------
Assembles the final AnalysisReport from the outputs of each pipeline stage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from .analyzer import SequenceAnalysis
from .models import (
    AnalysisReport,
    DeviceReport,
    DeviceStatus,
    Recommendation,
    RootCauseResult,
    SummaryReport,
)


def build_report(
    analyses: Dict[str, SequenceAnalysis],
    rca_results: Dict[str, RootCauseResult],
    recommendations: Dict[str, Recommendation],
    parse_errors: int = 0,
    total_events: int = 0,
) -> AnalysisReport:
    """
    Assemble the full :class:`AnalysisReport` from all pipeline outputs.

    Parameters
    ----------
    analyses:
        Per-device sequence analysis results.
    rca_results:
        Per-device root cause analysis results.
    recommendations:
        Per-device recommendation results.
    parse_errors:
        Number of lines that could not be parsed.
    total_events:
        Total number of successfully parsed events.
    """
    device_reports: List[DeviceReport] = []

    healthy_count = 0
    failed_count = 0
    degraded_count = 0

    for device_id, analysis in analyses.items():
        rca = rca_results.get(device_id)
        rec = recommendations.get(device_id)

        status = analysis.status
        if status == DeviceStatus.HEALTHY:
            healthy_count += 1
        elif status == DeviceStatus.FAILED:
            failed_count += 1
        elif status == DeviceStatus.DEGRADED:
            degraded_count += 1

        device_report = DeviceReport(
            device_id=device_id,
            status=status,
            event_count=analysis.event_count,
            first_seen=analysis.first_seen,
            last_seen=analysis.last_seen,
            failure_type=rca.failure_type if rca else None,
            retry_count=analysis.retry_count,
            confidence=rca.confidence if rca else None,
            probable_root_cause=rca.probable_root_cause if rca else None,
            supporting_evidence=rca.supporting_evidence if rca else [],
            recommendation=rec.recommendation if rec else [],
            failures=analysis.failures,
        )
        device_reports.append(device_report)

    # Sort: failed first, then degraded, then healthy (by device_id within group)
    _order = {DeviceStatus.FAILED: 0, DeviceStatus.DEGRADED: 1, DeviceStatus.HEALTHY: 2}
    device_reports.sort(key=lambda d: (_order.get(d.status, 3), d.device_id))

    summary = SummaryReport(
        total_devices=len(analyses),
        healthy_devices=healthy_count,
        failed_devices=failed_count,
        degraded_devices=degraded_count,
        total_events=total_events,
        parse_errors=parse_errors,
    )

    return AnalysisReport(
        generated_at=datetime.now(timezone.utc),
        summary=summary,
        devices=device_reports,
    )
