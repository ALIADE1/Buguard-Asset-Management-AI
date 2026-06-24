"""
Pydantic schemas for the AI / LangChain endpoints.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════
#  Natural-Language Query
# ═══════════════════════════════════════════════════════

class NLQueryRequest(BaseModel):
    """User's plain-English question about the asset inventory."""

    query: str = Field(..., min_length=3, max_length=2000)


class NLQueryResponse(BaseModel):
    """Structured result of a natural-language query."""

    original_query: str
    generated_sql: str
    explanation: str
    results: list[dict[str, Any]]
    total_results: int


# ═══════════════════════════════════════════════════════
#  Risk Scoring
# ═══════════════════════════════════════════════════════

class RiskScoreRequest(BaseModel):
    """Request risk assessment for an asset or group."""

    asset_ids: list[str] = Field(
        default_factory=list,
        description="Specific asset IDs to score. Empty = score everything.",
    )
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional filters: type, status, tags, etc.",
    )


class RiskScoreResponse(BaseModel):
    """LLM-generated risk assessment."""

    summary: str
    risk_level: str  # "critical" | "high" | "medium" | "low" | "info"
    findings: list[dict[str, Any]]
    recommendations: list[str]
    assets_analyzed: int


# ═══════════════════════════════════════════════════════
#  Enrichment & Categorization
# ═══════════════════════════════════════════════════════

class EnrichRequest(BaseModel):
    """Request automated enrichment for one or more assets."""

    asset_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Asset IDs to enrich.",
    )


class EnrichedAsset(BaseModel):
    """Enrichment results for a single asset."""

    asset_id: str
    environment: str  # "production" | "staging" | "development" | "unknown"
    category: str
    criticality: str  # "critical" | "high" | "medium" | "low"
    enriched_metadata: dict[str, Any]
    reasoning: str


class EnrichResponse(BaseModel):
    """Batch enrichment results."""

    enriched_assets: list[EnrichedAsset]
    total_processed: int


# ═══════════════════════════════════════════════════════
#  Report Generation
# ═══════════════════════════════════════════════════════

class ReportRequest(BaseModel):
    """Request a natural-language report."""

    scope: str = Field(
        default="all",
        description="'all' or a filter expression (e.g. type=subdomain, tag=production).",
    )
    report_type: str = Field(
        default="inventory",
        description="'inventory' | 'risk' | 'executive_summary'",
    )


class ReportResponse(BaseModel):
    """The generated report."""

    report_type: str
    scope: str
    report: str  # Markdown-formatted text
    generated_from_assets: int
