# Intelligent Event Analysis System

A Python application that analyses a stream of system events, identifies abnormal
behaviour, determines probable root causes, and provides actionable recommendations.
Exposed via a REST API built with FastAPI.

---

## Table of Contents

1. [Setup](#setup)
2. [Execution](#execution)
3. [API Reference](#api-reference)
4. [Architecture](#architecture)
5. [Design Decisions](#design-decisions)
6. [Scalability](#scalability)
7. [Limitations](#limitations)
8. [Optional – AI Extension](#optional--ai-extension)

---

## Setup

### Prerequisites

- Python 3.10 or later
- `pip`

### Install dependencies

```bash
# From the candidate_solution/ directory
pip install -r requirements.txt
```

---

## Execution

### Run the API server

```bash
# From the candidate_solution/ directory
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

Interactive API docs are available at:
- Swagger UI → http://localhost:8000/docs
- ReDoc      → http://localhost:8000/redoc

---

### Analyse the sample log via the API

#### Option A – submit raw log text

```bash
curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d "{\"log_text\": \"$(cat sample_data/events.log | tr '\n' '|' | sed 's/|/\\n/g')\"}" \
  | python -m json.tool
```

#### Option B – upload the log file

```bash
curl -s -X POST http://localhost:8000/analyze/upload \
  -F "file=@sample_data/events.log" \
  | python -m json.tool
```

#### Get summary

```bash
curl -s http://localhost:8000/summary | python -m json.tool
```

#### Get a specific device report

```bash
curl -s http://localhost:8000/device/DEVICE-02 | python -m json.tool
```

---

### Run the test suite

```bash
# From the candidate_solution/ directory
pytest tests/ -v
```

Sample output:

```
tests/test_parser.py::TestValidInput::test_single_valid_event           PASSED
tests/test_parser.py::TestValidInput::test_multiple_events_same_device  PASSED
...
tests/test_api.py::TestMultipleDevices::test_multiple_devices           PASSED
========================= 50 passed in 0.94s ==========================
```

---

### Generate a sample output file

```bash
python - <<'EOF'
import json
from src.parser import parse_log_text, count_parse_errors
from src.analyzer import analyze_all_devices
from src.root_cause import analyze_root_causes
from src.recommender import generate_all_recommendations
from src.report import build_report

with open("sample_data/events.log") as f:
    text = f.read()

device_events = parse_log_text(text)
analyses      = analyze_all_devices(device_events)
rca           = analyze_root_causes(analyses)
recs          = generate_all_recommendations(rca)
report        = build_report(analyses, rca, recs,
                             parse_errors=count_parse_errors(text),
                             total_events=sum(len(v) for v in device_events.values()))

with open("output/sample_output.json", "w") as f:
    f.write(report.model_dump_json(indent=2))

print("Written to output/sample_output.json")
EOF
```

---

## API Reference

| Method | Path                  | Description                                      |
|--------|-----------------------|--------------------------------------------------|
| GET    | `/health`             | Liveness probe                                   |
| POST   | `/analyze`            | Analyse raw log text (JSON body)                 |
| POST   | `/analyze/upload`     | Analyse an uploaded `.log` file (multipart)      |
| GET    | `/summary`            | Aggregated summary of the latest analysis        |
| GET    | `/summary?job_id=…`   | Summary for a specific job                       |
| GET    | `/device/{device_id}` | Detailed report for a device (latest analysis)   |
| GET    | `/jobs`               | List all stored job IDs                          |
| GET    | `/docs`               | Swagger UI                                       |

---

## Architecture

See [`architecture/architecture.md`](architecture/architecture.md) for the full
component diagram and data-flow chart.

**High-level pipeline:**

```
Log source
    │
    ▼
Event Parser        ← streaming, line-by-line, O(1) memory
    │
    ▼
Sequence Analyzer   ← 6 detection layers (not just "FAILED" keyword)
    │
    ▼
Root Cause Analyzer ← rule-based pattern library + confidence scoring
    │
    ▼
Recommendation Eng. ← maps patterns → ordered action lists
    │
    ▼
Report Assembler    ← produces structured JSON
    │
    ▼
FastAPI REST API    ← exposes all results via HTTP
```

---

## Design Decisions

### Event Parser

- **Streaming design**: The parser reads input line-by-line using Python generators.
  Only one line is ever held in memory at a time, enabling processing of arbitrarily
  large files.
- **Deduplication**: Events are deduplicated by `(timestamp, device_id, event_type, status)`.
- **Resilience**: Malformed lines are logged (with line number) and skipped rather than
  crashing the pipeline.
- **Format flexibility**: The `message` field is optional – any tokens between
  `event_type` and the final `status` token are captured as the message.

### Failure Detection

Six detection layers run independently and their results are merged:

| Layer | Technique |
|-------|-----------|
| Status failures | Checks each event's status against a known failure set (`FAILED`, `ERROR`, `TIMEOUT`) |
| Excessive retries | Counts `RETRY` events; flags if count ≥ threshold (default: 2) |
| Repeated failures | Tallies failures per event type; flags if same type fails > 1 time |
| Missing events | Compares observed successful events against the expected lifecycle flow |
| Unexpected ordering | Detects out-of-order events relative to the standard lifecycle |
| Long delays | Flags gaps between consecutive events that exceed a configurable threshold |

The system does **not** rely solely on the `FAILED` keyword. A device can be
marked FAILED or DEGRADED through any combination of the above layers.

### Root Cause Analysis

A **pattern library** (list of dicts in `root_cause.py`) maps failure signatures
to human-readable root cause descriptions and base confidence scores.

Confidence scoring:

```
confidence = base_confidence
           + 0.05 × (extra matching failure types beyond the minimum)
           − 0.05  (penalty if retry_count > 5, indicating high complexity)
```

The approach is intentionally rule-based and pluggable: the pattern library can be
loaded from YAML/JSON at runtime, and the `_score_pattern` function can be replaced
by a call to an ML classifier (e.g., scikit-learn, XGBoost) that accepts the same
feature dict without changing any other part of the pipeline.

### Recommendations

A simple `dict` mapping failure pattern names → ordered action lists. Easy to
extend, load from a config file, or replace with a RAG (Retrieval-Augmented
Generation) call to an LLM.

---

## Scalability

### 1 GB

The streaming parser already handles this efficiently. A single-process FastAPI
server with the current in-memory result store is sufficient.

### 10 GB

- Keep the streaming parser (no changes needed).
- Replace the in-memory result store with **Redis** (swap the `_result_store` dict
  for a Redis client; the API endpoints require no other changes).
- Use **chunked reading** in the parser: process N lines, write per-device
  partial results to Redis, merge at the end.
- Run multiple Uvicorn workers behind an nginx reverse proxy.

### 100 GB+

Distributed pipeline:

```
Log files (S3 / GCS / HDFS)
        │
        ▼
Apache Kafka  ← ingest events as messages
        │
        ▼
Apache Spark Structured Streaming
  or Apache Flink
        │ (stateful aggregation per device_id)
        ▼
Root cause micro-service (containerised Python)
        │
        ▼
Result store (DynamoDB / Cassandra)
        │
        ▼
FastAPI query layer (read-only, stateless)
        │
        ▼
Kubernetes + HPA for horizontal scaling
```

The analysis logic in `analyzer.py` and `root_cause.py` is stateless per device,
making it trivially parallelisable by device_id (Kafka partition key = device_id).

---

## Limitations

- **In-memory grouping**: After streaming, per-device event lists are held in RAM.
  For 100 GB+ inputs, these should be written to a database or distributed store.
- **Rule-based root cause**: Confidence scores are hand-tuned; they may not
  generalise well to unusual event patterns.
- **No persistence**: The result store is reset when the server restarts (Redis
  integration is the straightforward next step).
- **Single expected flow**: The system assumes one canonical lifecycle
  (`CONNECTION_START → AUTHENTICATION → SESSION_START → DATA_TRANSFER`).
  Device types with different flows would need additional flow definitions.
- **No authentication on the API**: For production use, add OAuth2 / API key
  authentication via FastAPI's security utilities.

---

## Optional – AI Extension

### Proposed AI-Powered Pipeline

```
Large-scale event stream (Kafka)
        │
        ▼
Event pre-processing (Spark Structured Streaming)
  • Feature extraction per device window
  • Rolling statistics (failure rate, retry frequency, inter-event delays)
        │
        ▼
Anomaly Detection (Isolation Forest / LSTM Autoencoder)
  • Trained on historical "healthy" event sequences
  • Flags statistical outliers without explicit rules
        │
        ▼
Context Retrieval (Vector DB – e.g. Pinecone / Weaviate)
  • Embeds current failure pattern using a sentence transformer
  • Retrieves similar past incidents and their resolutions
        │
        ▼
LLM-based Root Cause & Recommendation (GPT-4 / Gemini / Llama)
  • Prompt: anomaly features + retrieved context + recent raw events
  • Output: structured JSON with root cause, confidence, recommendations
        │
        ▼
Human-in-the-loop Review (optional escalation queue)
        │
        ▼
Feedback loop → retrain anomaly model + update vector store
```

### Technology Choices

| Component | Technology |
|-----------|-----------|
| Streaming ingestion | Apache Kafka |
| Stream processing | Apache Spark / Flink |
| Anomaly detection | scikit-learn Isolation Forest or PyTorch LSTM |
| Vector store | Pinecone / Weaviate / pgvector |
| LLM | Google Gemini / OpenAI GPT-4 / local Llama |
| Orchestration | Apache Airflow / Prefect |
| Serving | Kubernetes + FastAPI |

The current rule-based `root_cause.py` serves as an excellent baseline and
fallback: if the ML model's confidence is below a threshold, the rule engine
is used instead, ensuring deterministic behaviour even without a trained model.
