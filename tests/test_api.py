"""
test_api.py
-----------
Integration tests for the FastAPI endpoints.

Uses FastAPI TestClient so no real HTTP server is needed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import app, _result_store, _LATEST_KEY

client = TestClient(app)

# ---------------------------------------------------------------------------
# Sample log fixtures
# ---------------------------------------------------------------------------

HEALTHY_LOG = """
2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS
2026-09-01 10:00:02 DEVICE-01 AUTHENTICATION SUCCESS
2026-09-01 10:00:03 DEVICE-01 SESSION_START SUCCESS
2026-09-01 10:00:10 DEVICE-01 DATA_TRANSFER SUCCESS
""".strip()

FAILED_LOG = """
2026-09-01 10:01:01 DEVICE-02 CONNECTION_START SUCCESS
2026-09-01 10:01:02 DEVICE-02 AUTHENTICATION SUCCESS
2026-09-01 10:01:03 DEVICE-02 SESSION_START FAILED
2026-09-01 10:01:04 DEVICE-02 RETRY STARTED
2026-09-01 10:01:05 DEVICE-02 SESSION_START FAILED
2026-09-01 10:01:06 DEVICE-02 RETRY STARTED
2026-09-01 10:01:07 DEVICE-02 SESSION_START FAILED
""".strip()

MULTI_DEVICE_LOG = f"{HEALTHY_LOG}\n{FAILED_LOG}"

EMPTY_LOG = ""

INVALID_ONLY_LOG = "BADLINE\nANOTHER BAD ONE\n"

MIXED_LOG = f"BADLINE\n{HEALTHY_LOG}"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def post_analyze(log_text: str):
    return client.post("/analyze", json={"log_text": log_text})


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /analyze
# ---------------------------------------------------------------------------

class TestAnalyzeEndpoint:
    def setup_method(self):
        _result_store.clear()

    def test_valid_log_returns_200(self):
        resp = post_analyze(HEALTHY_LOG)
        assert resp.status_code == 200

    def test_response_has_job_id(self):
        resp = post_analyze(HEALTHY_LOG)
        data = resp.json()
        assert "job_id" in data
        assert len(data["job_id"]) == 36  # UUID format

    def test_response_has_report(self):
        resp = post_analyze(HEALTHY_LOG)
        data = resp.json()
        assert "report" in data
        assert data["report"] is not None

    def test_healthy_device_in_report(self):
        resp = post_analyze(HEALTHY_LOG)
        devices = resp.json()["report"]["devices"]
        assert any(d["device_id"] == "DEVICE-01" for d in devices)

    def test_healthy_device_status(self):
        resp = post_analyze(HEALTHY_LOG)
        devices = resp.json()["report"]["devices"]
        device = next(d for d in devices if d["device_id"] == "DEVICE-01")
        assert device["status"] == "HEALTHY"

    def test_failed_device_status(self):
        resp = post_analyze(FAILED_LOG)
        devices = resp.json()["report"]["devices"]
        device = next(d for d in devices if d["device_id"] == "DEVICE-02")
        assert device["status"] == "FAILED"

    def test_failed_device_has_recommendation(self):
        resp = post_analyze(FAILED_LOG)
        devices = resp.json()["report"]["devices"]
        device = next(d for d in devices if d["device_id"] == "DEVICE-02")
        assert len(device["recommendation"]) >= 1

    def test_empty_log_returns_200_empty_report(self):
        resp = post_analyze(EMPTY_LOG)
        assert resp.status_code == 200
        data = resp.json()
        assert data["report"]["summary"]["total_devices"] == 0

    def test_invalid_only_log_returns_empty_devices(self):
        resp = post_analyze(INVALID_ONLY_LOG)
        assert resp.status_code == 200
        assert resp.json()["report"]["summary"]["total_devices"] == 0

    def test_mixed_valid_invalid_log(self):
        resp = post_analyze(MIXED_LOG)
        assert resp.status_code == 200
        assert resp.json()["report"]["summary"]["total_devices"] >= 1

    def test_multiple_devices(self):
        resp = post_analyze(MULTI_DEVICE_LOG)
        summary = resp.json()["report"]["summary"]
        assert summary["total_devices"] == 2
        assert summary["healthy_devices"] == 1
        assert summary["failed_devices"] == 1

    def test_summary_counts_correct(self):
        resp = post_analyze(MULTI_DEVICE_LOG)
        s = resp.json()["report"]["summary"]
        assert s["total_devices"] == s["healthy_devices"] + s["failed_devices"] + s["degraded_devices"]


# ---------------------------------------------------------------------------
# GET /summary
# ---------------------------------------------------------------------------

class TestSummaryEndpoint:
    def setup_method(self):
        _result_store.clear()

    def test_summary_404_before_analysis(self):
        resp = client.get("/summary")
        assert resp.status_code == 404

    def test_summary_200_after_analysis(self):
        post_analyze(HEALTHY_LOG)
        resp = client.get("/summary")
        assert resp.status_code == 200

    def test_summary_has_correct_keys(self):
        post_analyze(HEALTHY_LOG)
        resp = client.get("/summary")
        data = resp.json()
        assert "total_devices" in data
        assert "healthy_devices" in data
        assert "failed_devices" in data

    def test_summary_by_job_id(self):
        post_resp = post_analyze(HEALTHY_LOG)
        job_id = post_resp.json()["job_id"]
        resp = client.get(f"/summary?job_id={job_id}")
        assert resp.status_code == 200

    def test_summary_invalid_job_id_404(self):
        resp = client.get("/summary?job_id=nonexistent-job-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /device/{device_id}
# ---------------------------------------------------------------------------

class TestDeviceEndpoint:
    def setup_method(self):
        _result_store.clear()

    def test_device_404_before_analysis(self):
        resp = client.get("/device/DEVICE-01")
        assert resp.status_code == 404

    def test_device_200_after_analysis(self):
        post_analyze(HEALTHY_LOG)
        resp = client.get("/device/DEVICE-01")
        assert resp.status_code == 200

    def test_device_case_insensitive(self):
        post_analyze(HEALTHY_LOG)
        resp = client.get("/device/device-01")
        assert resp.status_code == 200

    def test_device_has_correct_fields(self):
        post_analyze(HEALTHY_LOG)
        data = client.get("/device/DEVICE-01").json()
        assert "device_id" in data
        assert "status" in data
        assert "event_count" in data
        assert "recommendation" in data

    def test_nonexistent_device_404(self):
        post_analyze(HEALTHY_LOG)
        resp = client.get("/device/DEVICE-NONEXISTENT")
        assert resp.status_code == 404

    def test_failed_device_has_failure_type(self):
        post_analyze(FAILED_LOG)
        data = client.get("/device/DEVICE-02").json()
        assert data["failure_type"] is not None

    def test_failed_device_has_confidence(self):
        post_analyze(FAILED_LOG)
        data = client.get("/device/DEVICE-02").json()
        assert data["confidence"] is not None
        assert 0.0 <= data["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# GET /jobs
# ---------------------------------------------------------------------------

class TestJobsEndpoint:
    def setup_method(self):
        _result_store.clear()

    def test_jobs_initially_empty(self):
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert resp.json()["total_jobs"] == 0

    def test_jobs_increments_after_analyze(self):
        post_analyze(HEALTHY_LOG)
        post_analyze(FAILED_LOG)
        resp = client.get("/jobs")
        assert resp.json()["total_jobs"] == 2
