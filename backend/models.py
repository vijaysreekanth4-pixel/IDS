"""
models.py
---------
Pydantic data models for the Intelligent Event Analysis System.
All inter-module data exchange uses these typed models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EventStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    STARTED = "STARTED"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


class DeviceStatus(str, Enum):
    HEALTHY = "HEALTHY"
    FAILED = "FAILED"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Core Event
# ---------------------------------------------------------------------------

class Event(BaseModel):
    """A single parsed system event."""

    model_config = ConfigDict(frozen=True)  # make hashable for dedup sets

    timestamp: datetime
    device_id: str
    event_type: str
    message: Optional[str] = None
    status: str                          # raw string kept for flexibility
    line_number: Optional[int] = None    # source line for debugging


# ---------------------------------------------------------------------------
# Failure Models
# ---------------------------------------------------------------------------

class FailureDetail(BaseModel):
    """Describes a single detected failure on a device."""

    failure_type: str
    failure_event: str
    retry_count: int = 0
    failed_at: Optional[datetime] = None
    delay_seconds: Optional[float] = None
    missing_events: List[str] = Field(default_factory=list)
    unexpected_sequence: Optional[str] = None


class RootCauseResult(BaseModel):
    """Root cause analysis output for a device."""

    device_id: str
    status: DeviceStatus
    failure_type: Optional[str] = None
    failure_event: Optional[str] = None
    retry_count: int = 0
    probable_root_cause: Optional[str] = None
    confidence: float = 0.0
    supporting_evidence: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

class Recommendation(BaseModel):
    """Recommended actions for a device."""

    device_id: str
    recommendation: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-device Report
# ---------------------------------------------------------------------------

class DeviceReport(BaseModel):
    """Full analysis report for a single device."""

    device_id: str
    status: DeviceStatus
    event_count: int
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    failure_type: Optional[str] = None
    retry_count: int = 0
    confidence: Optional[float] = None
    probable_root_cause: Optional[str] = None
    supporting_evidence: List[str] = Field(default_factory=list)
    recommendation: List[str] = Field(default_factory=list)
    failures: List[FailureDetail] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class SummaryReport(BaseModel):
    """Aggregated summary across all devices."""

    total_devices: int
    healthy_devices: int
    failed_devices: int
    degraded_devices: int
    total_events: int
    parse_errors: int


# ---------------------------------------------------------------------------
# Full Analysis Report
# ---------------------------------------------------------------------------

class AnalysisReport(BaseModel):
    """Top-level JSON report returned by the analysis engine."""

    generated_at: datetime = Field(default_factory=datetime.utcnow)
    summary: SummaryReport
    devices: List[DeviceReport]


# ---------------------------------------------------------------------------
# API Request / Response
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    """Request body for POST /analyze when sending raw log text."""

    log_text: str = Field(..., description="Raw event log content as a string")


class AnalyzeResponse(BaseModel):
    """Response body for POST /analyze."""

    job_id: str
    status: str
    message: str
    report: Optional[AnalysisReport] = None
