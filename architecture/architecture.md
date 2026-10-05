# Architecture – Intelligent Event Analysis System

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        CLIENT  (curl / browser / other service)         │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │  HTTP
                         ┌──────────▼──────────┐
                         │    FastAPI REST API   │
                         │  POST /analyze        │
                         │  POST /analyze/upload │
                         │  GET  /summary        │
                         │  GET  /device/{id}    │
                         │  GET  /jobs           │
                         │  GET  /health         │
                         └──────────┬────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │         Analysis Pipeline      │
                    │                                │
                    │  ┌─────────────────────────┐  │
                    │  │  1. Event Parser         │  │
                    │  │  • Line-by-line stream   │  │
                    │  │  • Validates fields      │  │
                    │  │  • Deduplicates events   │  │
                    │  │  • Sorts per device      │  │
                    │  └────────────┬────────────┘  │
                    │               │                │
                    │  ┌────────────▼────────────┐  │
                    │  │  2. Sequence Analyzer   │  │
                    │  │  • Groups by device     │  │
                    │  │  • 6 detection layers:  │  │
                    │  │    - Status failures    │  │
                    │  │    - Repeated failures  │  │
                    │  │    - Excessive retries  │  │
                    │  │    - Missing events     │  │
                    │  │    - Unexpected order   │  │
                    │  │    - Long delays        │  │
                    │  └────────────┬────────────┘  │
                    │               │                │
                    │  ┌────────────▼────────────┐  │
                    │  │  3. Root Cause Analyzer │  │
                    │  │  • Pattern library      │  │
                    │  │  • Confidence scoring   │  │
                    │  │  • Evidence collection  │  │
                    │  └────────────┬────────────┘  │
                    │               │                │
                    │  ┌────────────▼────────────┐  │
                    │  │  4. Recommendation Eng. │  │
                    │  │  • Maps patterns →      │  │
                    │  │    ordered action list  │  │
                    │  └────────────┬────────────┘  │
                    │               │                │
                    │  ┌────────────▼────────────┐  │
                    │  │  5. Report Assembler    │  │
                    │  │  • Builds AnalysisReport│  │
                    │  │  • Sorts: FAILED first  │  │
                    │  └─────────────────────────┘  │
                    └───────────────────────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │   In-Memory Result Store        │
                    │   (dict: job_id → Report)       │
                    │   (Redis-ready interface)        │
                    └───────────────────────────────┘
```

## Scalability Design

```
Current (≤1 GB)          Scale to 10 GB              Scale to 100 GB+
─────────────────         ──────────────────           ──────────────────────
Single process            Single process               Distributed pipeline
Streaming parser          Streaming parser             Apache Kafka ingestion
In-memory grouping        Chunked grouping             Apache Spark / Flink
FastAPI server            FastAPI + Redis              Kubernetes microservices
                          result store                 Distributed result store
                                                       (DynamoDB / Cassandra)
```

## Data Flow

```
events.log (any size)
      │
      │ line-by-line (O(1) memory)
      ▼
  stream_events()
      │
      │ yields Event objects
      ▼
  parse_log_text() / parse_log_file()
      │
      │ Dict[device_id → List[Event]] (sorted)
      ▼
  analyze_all_devices()
      │
      │ Dict[device_id → SequenceAnalysis]
      ▼
  analyze_root_causes()
      │
      │ Dict[device_id → RootCauseResult]
      ▼
  generate_all_recommendations()
      │
      │ Dict[device_id → Recommendation]
      ▼
  build_report()
      │
      │ AnalysisReport (JSON-serializable)
      ▼
  API response / file output
```
