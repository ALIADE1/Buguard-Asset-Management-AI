"""
Prompt templates for the four AI capabilities.

Every prompt is engineered around a core principle:
  **Ground all outputs strictly in the provided data.**

Each template receives real database content — serialised assets, schema
DDL, or query results — and the LLM is instructed to produce answers
derived exclusively from that data.
"""

# ═══════════════════════════════════════════════════════
#  Shared: Database Schema Reference (fed into text-to-SQL)
# ═══════════════════════════════════════════════════════

DB_SCHEMA_DESCRIPTION = """\
PostgreSQL database schema for an Attack Surface Management (ASM) system.

TABLE: assets
  - id              UUID PRIMARY KEY
  - type            ENUM('domain','subdomain','ip_address','service','certificate','technology')
  - value           VARCHAR(2048)        -- canonical value, e.g. "api.example.com", "203.0.113.10"
  - status          ENUM('active','stale','archived')
  - first_seen      TIMESTAMPTZ
  - last_seen       TIMESTAMPTZ
  - source          ENUM('import','scan','manual')
  - tags            TEXT[]               -- PostgreSQL array, e.g. '{production,api}'
  - metadata        JSONB                -- type-specific fields (cert expiry, port, version, etc.)

TABLE: asset_relationships
  - id                  UUID PRIMARY KEY
  - source_asset_id     UUID REFERENCES assets(id)
  - target_asset_id     UUID REFERENCES assets(id)
  - relationship_type   VARCHAR(64)      -- 'belongs_to','resolves_to','runs_on','covers','detected_on'
  - metadata            JSONB
  - created_at          TIMESTAMPTZ

UNIQUE INDEX: (assets.type, assets.value)
UNIQUE INDEX: (asset_relationships.source_asset_id, asset_relationships.target_asset_id, asset_relationships.relationship_type)

IMPORTANT NOTES for writing SQL:
  - Tags column is a PostgreSQL TEXT array. Use '@>' operator for "contains" checks.
    Example: tags @> ARRAY['production']::text[]
  - Metadata is JSONB. Access nested fields with ->> (text) or -> (json).
    Example: metadata->>'environment' = 'production'
    Example: (metadata->>'expired')::boolean = true
    Example: metadata->>'not_after' for certificate expiry date
  - For case-insensitive matching on value, use ILIKE.
  - All timestamps are timezone-aware (TIMESTAMPTZ).
"""


# ═══════════════════════════════════════════════════════
#  1) Natural-Language → SQL
# ═══════════════════════════════════════════════════════

TEXT_TO_SQL_PROMPT = """\
You are an expert PostgreSQL query writer for an Attack Surface Management (ASM) database.

DATABASE SCHEMA:
{schema}

USER QUESTION:
{question}

INSTRUCTIONS:
1. Write a single, read-only PostgreSQL SELECT query that answers the user's question.
2. You MUST ONLY use SELECT statements. Never use INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, or any data-modifying statement.
3. Use proper PostgreSQL syntax for array operations (e.g., @> for array contains) and JSONB access (->>, ->).
4. If the question is ambiguous, make reasonable assumptions and explain them.
5. If the question is completely unrelated to the ASM database (e.g., about weather, politics, cooking), respond with EXACTLY:
   SQL: NONE
   EXPLANATION: This question is outside the scope of the asset management database.
6. Limit results to 100 rows maximum using LIMIT 100.
7. Always include the asset's id, type, value, and status columns when querying the assets table.

Respond in EXACTLY this format (no markdown fencing):
SQL: <your SELECT query here>
EXPLANATION: <brief explanation of what the query does and any assumptions made>
"""


# ═══════════════════════════════════════════════════════
#  2) Risk Scoring & Summarization
# ═══════════════════════════════════════════════════════

RISK_SCORING_PROMPT = """\
You are a cybersecurity risk analyst for an Attack Surface Management (ASM) platform.

Analyze the following assets from the database and produce a risk assessment.
Base your analysis EXCLUSIVELY on the provided asset data. Do NOT invent or assume any information not present in the data.

ASSET DATA:
{asset_data}

INSTRUCTIONS:
1. Identify concrete security risks present in the data (e.g., expired certificates, exposed sensitive services like SSH/RDP/databases, stale assets, missing security controls).
2. Assign an overall risk level: "critical", "high", "medium", "low", or "info".
3. List specific findings with the affected asset values.
4. Provide actionable recommendations.
5. Do NOT hallucinate — only report risks that are evidenced by the actual data fields above.

Respond in EXACTLY this JSON format (no markdown fencing, no extra text):
{{
  "summary": "<2-3 sentence overall risk summary>",
  "risk_level": "<critical|high|medium|low|info>",
  "findings": [
    {{
      "title": "<finding title>",
      "severity": "<critical|high|medium|low>",
      "affected_assets": ["<asset value 1>", "<asset value 2>"],
      "detail": "<explanation of the risk>"
    }}
  ],
  "recommendations": [
    "<actionable recommendation 1>",
    "<actionable recommendation 2>"
  ]
}}
"""


# ═══════════════════════════════════════════════════════
#  3) Enrichment & Categorization
# ═══════════════════════════════════════════════════════

ENRICHMENT_PROMPT = """\
You are an infrastructure analyst for an Attack Surface Management (ASM) platform.

Analyze the following asset and classify it based on its value, type, tags, and metadata.
Base your analysis EXCLUSIVELY on the provided asset data. Do NOT invent information.

ASSET DATA:
  ID: {asset_id}
  Type: {asset_type}
  Value: {asset_value}
  Status: {asset_status}
  Tags: {asset_tags}
  Current Metadata: {asset_metadata}

INSTRUCTIONS:
1. Determine the environment: "production", "staging", "development", or "unknown".
   - Look for clues in the value (e.g., "prod", "staging", "dev", "test" in subdomains), tags, and metadata.
2. Determine a descriptive category for this asset (e.g., "Web Server", "API Endpoint", "DNS Record", "TLS Certificate", "Database Service", "CDN", etc.).
3. Determine criticality: "critical", "high", "medium", or "low".
   - Production assets with sensitive services → critical/high.
   - Staging/dev assets → medium/low.
   - Expired or misconfigured assets → raise criticality.
4. Suggest additional metadata fields that can be inferred from the existing data. Only add fields that are defensible from the data.
5. Provide brief reasoning for each classification.

Respond in EXACTLY this JSON format (no markdown fencing, no extra text):
{{
  "environment": "<production|staging|development|unknown>",
  "category": "<descriptive category>",
  "criticality": "<critical|high|medium|low>",
  "enriched_metadata": {{
    "<key>": "<value>"
  }},
  "reasoning": "<brief explanation of your classifications>"
}}
"""


# ═══════════════════════════════════════════════════════
#  4) Report Generation
# ═══════════════════════════════════════════════════════

REPORT_GENERATION_PROMPT = """\
You are a professional cybersecurity report writer for an Attack Surface Management (ASM) platform.

Generate a {report_type} report based EXCLUSIVELY on the following asset data.
Do NOT invent or assume any information not present in the data.

REPORT SCOPE: {scope}

ASSET DATA SUMMARY:
- Total assets: {total_assets}
- By type: {type_breakdown}
- By status: {status_breakdown}

DETAILED ASSET DATA:
{asset_data}

INSTRUCTIONS:
1. Write a professional, well-structured Markdown report.
2. If report_type is "inventory": Focus on cataloguing the assets, their types, statuses, and relationships.
3. If report_type is "risk": Focus on security risks, vulnerabilities, expired certificates, exposed services, and recommendations.
4. If report_type is "executive_summary": Provide a high-level overview suitable for management, with key metrics and top priorities.
5. Include specific asset values and concrete data points — do NOT be vague or generic.
6. Use tables where appropriate for structured data.
7. Base EVERY statement on the actual data provided. If you cannot determine something from the data, say so explicitly.

Generate the report now:
"""
