# DarkAtlas Asset Management AI Module

DarkAtlas is a high-performance, asynchronous, self-contained **Asset Management System** module designed for Attack Surface Monitoring (ASM) platforms. It tracks domains, subdomains, IP addresses, services, certificates, and technologies while leveraging **LangChain** and **Groq (Llama 3 70B)** to provide natural-language querying, risk scoring, automated metadata enrichment, and security reporting.

---

## 🚀 Key Architectural & Design Decisions

### 1. Asynchronous Foundation (FastAPI + SQLAlchemy + asyncpg)
The entire application is written using Python's `asyncio` stack:
- **FastAPI**: Handles high-concurrency HTTP routing.
- **SQLAlchemy 2.0 (Asyncio)**: Implements async object-relational mapping.
- **asyncpg**: Connects to PostgreSQL using binary protocol execution for optimal performance, minimizing overhead and CPU utilization compared to synchronous drivers.

### 2. Idempotent Deduplication (ON CONFLICT UPSERT)
To support scaleable, stateless scanner integrations, all asset ingestion is idempotent:
- A composite unique index on `(type, value)` acts as the natural primary key constraint.
- Re-importing an existing asset executes a PostgreSQL `INSERT ... ON CONFLICT (type, value) DO UPDATE`.
- This automatically updates `last_seen`, unions the new tags with existing tags, reactivates `stale` assets back to `active`, and performs a deep recursive JSON merge on asset metadata.

### 3. Directed Adjacency Relation Table (Many-to-Many Relationships)
Asset relationships (e.g., `api.example.com` **resolves_to** `203.0.113.10`, or `113.10` **runs_on** `SSH`) are stored in a normalized `asset_relationships` join table using a Directed Adjacency list:
- Columns: `source_asset_id` (UUID), `target_asset_id` (UUID), and `relationship_type` (String).
- A unique index on `(source_asset_id, target_asset_id, relationship_type)` ensures relationships are never duplicated.
- This design scales efficiently for complex graph traversals (parent domains, resolving IPs, detected open ports) and maintains database integrity.

### 4. Fast, Cost-Effective AI Reasoning via Groq (Llama 3 70B)
Instead of relying on high-latency or expensive closed models, DarkAtlas integrates **ChatGroq** using the `llama3-70b-8192` model:
- **Fast Inference**: Achieves ultra-low response times for natural language analysis.
- **Strict Read-Only SQL Execution**: Natural language queries are parsed into SQL and executed inside a PostgreSQL sub-transaction prefixed by `SET TRANSACTION READ ONLY`. A strict keyword blacklist (`INSERT`, `UPDATE`, `DELETE`, etc.) blocks any destructive queries before execution.

---

## 🛠️ Tech Stack

- **Backend Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Python 3.12)
- **Database**: [PostgreSQL 16](https://www.postgresql.org/)
- **ORM & Drivers**: [SQLAlchemy 2.0 Async](https://www.sqlalchemy.org/) & [asyncpg](https://github.com/MagicStack/asyncpg)
- **AI Framework**: [LangChain](https://www.langchain.com/) & [ChatGroq](https://github.com/langchain-ai/langchain-groq)
- **Deployment**: [Docker](https://www.docker.com/) & [Docker Compose](https://docs.docker.com/compose/)

---

## 📦 Setup & Installation Instructions

### Prerequisites
- Docker & Docker Compose installed on your host machine.
- A Groq API Key (Sign up at [console.groq.com](https://console.groq.com/)).

### 1. Configure the Environment
Create a `.env` file in the root directory by copying the example:
```bash
cp .env.example .env
```
Open `.env` and fill in your Groq API key:
```env
GROQ_API_KEY=gsk_your_groq_api_key_here
LLM_MODEL=llama3-70b-8192
POSTGRES_USER=asm_user
POSTGRES_PASSWORD=asm_password
POSTGRES_DB=asm_db
```

### 2. Run the Application
Start the system in detached mode:
```bash
docker-compose up --build -d
```
FastAPI automatically performs database migration/creation on startup inside the container.

- **FastAPI API Server**: `http://localhost:8000`
- **Interactive Swagger Docs**: `http://localhost:8000/docs`
- **Health Check**: `http://localhost:8000/health`

---

## 🔌 API Documentation

### Asset Ingestion & CRUD Endpoints

| Method | Endpoint | Description |
|:---|:---|:---|
| `POST` | `/api/v1/assets/import` | Strict bulk import. Validates all items; fails entire batch if any schema is malformed. |
| `POST` | `/api/v1/assets/import/raw` | Lenient bulk import. Processes each item individually, skipping bad records and collecting errors. |
| `GET` | `/api/v1/assets/` | Paginated listing with query-param filters (`type`, `status`, `source`, `tag`, `search`). |
| `GET` | `/api/v1/assets/{asset_id}` | Retrieve details for a single asset, including all active relationships. |

### LangChain AI Endpoints

| Method | Endpoint | Description |
|:---|:---|:---|
| `POST` | `/api/v1/ai/query` | Natural-Language query: translates text into SQL, executes read-only, returns matches. |
| `POST` | `/api/v1/ai/risk-score` | Analyzes assets for vulnerabilities, expired certs, or exposed services; returns risk score. |
| `POST` | `/api/v1/ai/enrich` | Evaluates assets to classify environment, criticality, category, and inject enriched metadata. |
| `POST` | `/api/v1/ai/report` | Compiles a professional security markdown report summarizing a selected asset scope. |

---

## 🤖 AI Track Showcase

### 1. Natural Language Asset Query
- **Endpoint**: `/api/v1/ai/query`
- **Request Payload**:
```json
{
  "query": "show me all active subdomains that have production in their tags"
}
```
- **Expected Response**:
```json
{
  "original_query": "show me all active subdomains that have production in their tags",
  "generated_sql": "SELECT id, type, value, status, tags FROM assets WHERE type = 'subdomain' AND status = 'active' AND tags @> ARRAY['production']::text[] LIMIT 100",
  "explanation": "Filters the assets table for rows where the asset type is 'subdomain', status is 'active', and the tags array contains the element 'production'.",
  "results": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "type": "subdomain",
      "value": "api.example.com",
      "status": "active",
      "tags": ["production", "api"]
    }
  ],
  "total_results": 1
}
```

### 2. Risk Scoring & Summarization
- **Endpoint**: `/api/v1/ai/risk-score`
- **Request Payload** (omitting `asset_ids` processes the whole database):
```json
{
  "asset_ids": ["550e8400-e29b-41d4-a716-446655440000", "770e8400-e29b-41d4-a716-446655440111"],
  "filters": {}
}
```
- **Expected Response**:
```json
{
  "summary": "The analyzed assets contain significant security exposure, notably an expired SSL certificate and an exposed administrative interface open to the public internet.",
  "risk_level": "critical",
  "findings": [
    {
      "asset_value": "api.example.com",
      "risk": "Expired Wildcard Certificate",
      "detail": "Certificate expired on 2026-05-10. Active production subdomains are serving traffic with invalid SSL parameters."
    },
    {
      "asset_value": "203.0.113.10",
      "risk": "Exposed SSH service",
      "detail": "SSH service is publicly accessible on port 22 with password authentication permitted in metadata."
    }
  ],
  "recommendations": [
    "Renew the SSL certificate for *.example.com immediately.",
    "Restrict SSH access on 203.0.113.10 to the corporate VPN using firewall rules."
  ],
  "assets_analyzed": 2
}
```

### 3. Automated Enrichment & Categorization
- **Endpoint**: `/api/v1/ai/enrich`
- **Request Payload**:
```json
{
  "asset_ids": ["550e8400-e29b-41d4-a716-446655440000"]
}
```
- **Expected Response**:
```json
{
  "enriched_assets": [
    {
      "asset_id": "550e8400-e29b-41d4-a716-446655440000",
      "environment": "production",
      "category": "Application Programming Interface (API)",
      "criticality": "high",
      "enriched_metadata": {
        "data_classification": "PII / Financial",
        "waf_protected": false,
        "primary_owner": "Payments Team"
      },
      "reasoning": "Asset serves as the main API endpoint ('api.example.com'), carries production tags, and contains endpoint routes handling transactions."
    }
  ],
  "total_processed": 1
}
```

### 4. Automated Report Generation
- **Endpoint**: `/api/v1/ai/report`
- **Request Payload**:
```json
{
  "scope": "tag=production",
  "report_type": "risk"
}
```
- **Expected Response**:
```json
{
  "report_type": "risk",
  "scope": "tag=production",
  "report": "# Attack Surface Risk Report: tag=production\n\n## Executive Summary\nThis report analyzes the risk posture of the production environment, covering 5 active assets.\n\n## Asset Breakdown\n- **Subdomains**: 2\n- **IP Addresses**: 1\n- **Services**: 2\n\n## Key Security Risks Identified\n1. **Expired SSL Certificate (`*.example.com`)**:\n   - *Impact*: High. Users will see security warnings, breaking client trust and programmatic API handshakes.\n2. **Public SSH Service (`203.0.113.10:22`)**:\n   - *Impact*: Critical. Vulnerable to credential brute-forcing.\n\n## Action Items\n- [ ] Renew the certificate.\n- [ ] Firewalls rules must lock port 22 on all production IPs.",
  "generated_from_assets": 5
}
```

---

## 🛡️ Assumptions & Edge Cases Handled

### Metadata Merging
When merging metadata JSONB payloads from different scanners, a recursive `deep_merge` algorithm is applied:
- **Divergent Scalars**: If Source A has `"environment": "dev"` and Source B imports `"environment": "prod"`, the latest write wins (`prod`).
- **Lists/Arrays**: If metadata includes lists (e.g. `["port-scanner", "shodan"]` and `["censys"]`), they are merged into a unique union list (`["port-scanner", "shodan", "censys"]`).
- **Dictionaries**: Child objects are merged recursively instead of being completely overridden.

### Reactivation of Stale Assets
When an asset is re-sighted, if its current status is `stale` or `archived`, it is automatically reactivated to `active` inside the atomic `ON CONFLICT` database operation.

### Malformed Ingests
The lenient `/import/raw` endpoint wraps each asset validation in a `try/except` block. If validation fails for a single asset, it records the validation failure message and continues processing the rest of the batch, guaranteeing that a single bad scanner output record does not drop the entire import pipeline.

### Grounding Against Hallucinations
All LLM prompts are heavily structured with grounding rules: the LLM is explicitly forbidden from inventing any metrics, assets, or risks. It is instructed to base its output strictly on the database schema DDL or JSON serialize payload passed directly into the prompt context.