"""
FastAPI router exposing the four LangChain-powered AI capabilities.

Endpoints:
  POST /api/v1/ai/query      — Natural-language query translated to safe SELECT queries.
  POST /api/v1/ai/risk-score — Assessment of security risks on specified assets.
  POST /api/v1/ai/enrich     — Automated asset classification and metadata enrichment.
  POST /api/v1/ai/report     — Generated professional markdown analysis reports.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.ai import (
    EnrichRequest,
    EnrichResponse,
    NLQueryRequest,
    NLQueryResponse,
    ReportRequest,
    ReportResponse,
    RiskScoreRequest,
    RiskScoreResponse,
)
from app.services.ai_service import (
    enrich_assets,
    generate_report,
    natural_language_query,
    risk_scoring,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/query",
    response_model=NLQueryResponse,
    status_code=200,
    summary="Natural-language asset query",
    description=(
        "Translates a plain-English query into a safe read-only SQL SELECT query, "
        "executes it, and returns the matching assets along with an explanation."
    ),
)
async def query_endpoint(
    payload: NLQueryRequest,
    db: AsyncSession = Depends(get_db),
) -> NLQueryResponse:
    try:
        return await natural_language_query(db, payload.query)
    except Exception as exc:
        logger.error("Error in natural_language_query: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to execute natural language query: {exc}",
        )


@router.post(
    "/risk-score",
    response_model=RiskScoreResponse,
    status_code=200,
    summary="Risk scoring & summarization",
    description=(
        "Evaluates the specified assets (or all if none specified) for security risks "
        "and returns a summary, risk level, specific findings, and recommendations."
    ),
)
async def risk_scoring_endpoint(
    payload: RiskScoreRequest,
    db: AsyncSession = Depends(get_db),
) -> RiskScoreResponse:
    try:
        return await risk_scoring(db, asset_ids=payload.asset_ids, filters=payload.filters)
    except Exception as exc:
        logger.error("Error in risk_scoring: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to run risk scoring analysis: {exc}",
        )


@router.post(
    "/enrich",
    response_model=EnrichResponse,
    status_code=200,
    summary="Automated enrichment & categorization",
    description=(
        "Uses the LLM to classify assets into environments, categories, and criticality levels, "
        "returning enriched metadata for each asset."
    ),
)
async def enrich_endpoint(
    payload: EnrichRequest,
    db: AsyncSession = Depends(get_db),
) -> EnrichResponse:
    try:
        return await enrich_assets(db, asset_ids=payload.asset_ids)
    except Exception as exc:
        logger.error("Error in enrich_assets: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to perform asset enrichment: {exc}",
        )


@router.post(
    "/report",
    response_model=ReportResponse,
    status_code=200,
    summary="Natural-language report generation",
    description=(
        "Generates a comprehensive markdown report for a given subset of assets "
        "based on their type, status, and tag scope."
    ),
)
async def report_endpoint(
    payload: ReportRequest,
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    try:
        return await generate_report(db, scope=payload.scope, report_type=payload.report_type)
    except Exception as exc:
        logger.error("Error in generate_report: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate report: {exc}",
        )
