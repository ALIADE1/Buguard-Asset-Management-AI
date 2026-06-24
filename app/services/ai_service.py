"""
AI service — four LangChain-powered analysis capabilities.

Each function:
  1. Fetches real data from PostgreSQL.
  2. Constructs a grounded prompt with that data.
  3. Invokes ChatGroq via LangChain.
  4. Parses and validates the LLM response.
  5. Returns a typed Pydantic schema.

All SQL execution is strictly read-only (SELECT) to prevent injection.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import get_llm
from app.ai.prompts import (
    DB_SCHEMA_DESCRIPTION,
    ENRICHMENT_PROMPT,
    REPORT_GENERATION_PROMPT,
    RISK_SCORING_PROMPT,
    TEXT_TO_SQL_PROMPT,
)
from app.schemas.ai import (
    EnrichedAsset,
    EnrichResponse,
    NLQueryResponse,
    ReportResponse,
    RiskScoreResponse,
)
from app.services.asset_service import get_all_assets_raw, get_asset_by_id

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════

# SQL keywords that are NEVER allowed in generated queries.
_FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|EXECUTE|CALL"
    r"|COPY|LOAD|MERGE)\b",
    re.IGNORECASE,
)


def _validate_readonly_sql(sql: str) -> str:
    """
    Ensure the generated SQL is a read-only SELECT statement.

    Raises ValueError if it contains any data-modifying keywords or
    is not a SELECT.
    """
    stripped = sql.strip().rstrip(";").strip()

    if not stripped.upper().startswith("SELECT"):
        raise ValueError(
            f"Generated SQL must start with SELECT. Got: {stripped[:60]}…"
        )

    match = _FORBIDDEN_SQL.search(stripped)
    if match:
        raise ValueError(
            f"Generated SQL contains forbidden keyword: {match.group()}"
        )

    # Prevent multiple statements (stacked queries)
    # Allow semicolons only at the very end (already stripped above)
    if ";" in stripped:
        raise ValueError("Generated SQL contains multiple statements.")

    return stripped


def _serialize_asset(asset: Any) -> dict[str, Any]:
    """Convert an ORM Asset to a plain dict suitable for LLM context."""
    return {
        "id": str(asset.id),
        "type": asset.type,
        "value": asset.value,
        "status": asset.status,
        "first_seen": asset.first_seen.isoformat() if asset.first_seen else None,
        "last_seen": asset.last_seen.isoformat() if asset.last_seen else None,
        "source": asset.source,
        "tags": asset.tags or [],
        "metadata": asset.metadata_ or {},
    }


def _serialize_assets_for_prompt(assets: list) -> str:
    """Serialize a list of ORM assets into a compact JSON string for LLM prompts."""
    serialized = [_serialize_asset(a) for a in assets]
    return json.dumps(serialized, indent=2, default=str)


def _safe_parse_json(text: str) -> dict[str, Any]:
    """
    Attempt to parse JSON from LLM output, handling common issues
    like markdown fencing or leading/trailing text.
    """
    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (```json or ```)
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    # Find the first { and last } to extract JSON object
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    return json.loads(cleaned)


# ═══════════════════════════════════════════════════════
#  1) Natural-Language → SQL Query
# ═══════════════════════════════════════════════════════

async def natural_language_query(
    session: AsyncSession,
    query: str,
) -> NLQueryResponse:
    """
    Translate a plain-English question into SQL, validate it, execute it,
    and return the results.
    """
    llm = get_llm()

    # ── Ask the LLM to generate SQL ───────────────────
    prompt = TEXT_TO_SQL_PROMPT.format(
        schema=DB_SCHEMA_DESCRIPTION,
        question=query,
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    raw_output = response.content.strip()

    logger.info("LLM Text-to-SQL output:\n%s", raw_output)

    # ── Parse the response ────────────────────────────
    sql_match = re.search(r"SQL:\s*(.+?)(?:\nEXPLANATION:|\Z)", raw_output, re.DOTALL)
    expl_match = re.search(r"EXPLANATION:\s*(.+)", raw_output, re.DOTALL)

    generated_sql = sql_match.group(1).strip() if sql_match else ""
    explanation = expl_match.group(1).strip() if expl_match else ""

    # ── Handle out-of-scope queries ───────────────────
    if generated_sql.upper() == "NONE" or not generated_sql:
        return NLQueryResponse(
            original_query=query,
            generated_sql="",
            explanation=explanation or "This question is outside the scope of the asset management database.",
            results=[],
            total_results=0,
        )

    # ── Validate SQL safety ───────────────────────────
    try:
        validated_sql = _validate_readonly_sql(generated_sql)
    except ValueError as e:
        logger.warning("SQL validation failed: %s — SQL: %s", e, generated_sql)
        return NLQueryResponse(
            original_query=query,
            generated_sql=generated_sql,
            explanation=f"Generated SQL was rejected for safety: {e}",
            results=[],
            total_results=0,
        )

    # ── Execute read-only in a sub-transaction ────────
    try:
        # Use a read-only connection to add an extra safety layer
        result = await session.execute(
            sa_text(f"SET TRANSACTION READ ONLY; {validated_sql}")
        )
        # The SET TRANSACTION returns nothing; the SELECT follows.
        # Actually, we need to execute them separately:
    except Exception:
        pass  # Fall through to the separate execution below

    try:
        # Execute in a readonly transaction
        await session.execute(sa_text("BEGIN"))
        await session.execute(sa_text("SET TRANSACTION READ ONLY"))
        result = await session.execute(sa_text(validated_sql))
        rows = result.mappings().all()
        await session.execute(sa_text("COMMIT"))

        # Convert rows to serializable dicts
        results = [
            {k: (v.isoformat() if isinstance(v, datetime) else str(v) if isinstance(v, UUID) else v)
             for k, v in dict(row).items()}
            for row in rows
        ]

        return NLQueryResponse(
            original_query=query,
            generated_sql=validated_sql,
            explanation=explanation,
            results=results,
            total_results=len(results),
        )
    except Exception as exc:
        logger.warning("SQL execution error: %s — SQL: %s", exc, validated_sql)
        # Try to rollback the failed transaction
        try:
            await session.execute(sa_text("ROLLBACK"))
        except Exception:
            pass
        return NLQueryResponse(
            original_query=query,
            generated_sql=validated_sql,
            explanation=f"Query generated but failed to execute: {exc}",
            results=[],
            total_results=0,
        )


# ═══════════════════════════════════════════════════════
#  2) Risk Scoring & Summarization
# ═══════════════════════════════════════════════════════

async def risk_scoring(
    session: AsyncSession,
    asset_ids: list[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> RiskScoreResponse:
    """
    Fetch assets, send their data to the LLM for risk analysis,
    and return a structured risk assessment.
    """
    # ── Fetch assets ──────────────────────────────────
    if asset_ids:
        assets = []
        for aid in asset_ids:
            try:
                asset = await get_asset_by_id(session, UUID(aid))
                if asset:
                    assets.append(asset)
            except (ValueError, Exception) as exc:
                logger.warning("Invalid asset ID '%s': %s", aid, exc)
    else:
        flt = filters or {}
        assets = await get_all_assets_raw(
            session,
            asset_type=flt.get("type"),
            status=flt.get("status"),
            tag=flt.get("tag"),
        )

    if not assets:
        return RiskScoreResponse(
            summary="No assets found matching the given criteria.",
            risk_level="info",
            findings=[],
            recommendations=["Import assets before requesting a risk assessment."],
            assets_analyzed=0,
        )

    # ── Build prompt ──────────────────────────────────
    asset_data = _serialize_assets_for_prompt(assets)
    prompt = RISK_SCORING_PROMPT.format(asset_data=asset_data)

    llm = get_llm()
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    raw_output = response.content.strip()

    logger.info("LLM Risk Scoring output:\n%s", raw_output)

    # ── Parse JSON response ───────────────────────────
    try:
        parsed = _safe_parse_json(raw_output)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to parse risk scoring JSON: %s", exc)
        return RiskScoreResponse(
            summary=raw_output[:500],
            risk_level="medium",
            findings=[],
            recommendations=["LLM response could not be parsed. Review raw output."],
            assets_analyzed=len(assets),
        )

    return RiskScoreResponse(
        summary=parsed.get("summary", ""),
        risk_level=parsed.get("risk_level", "medium"),
        findings=parsed.get("findings", []),
        recommendations=parsed.get("recommendations", []),
        assets_analyzed=len(assets),
    )


# ═══════════════════════════════════════════════════════
#  3) Automated Enrichment & Categorization
# ═══════════════════════════════════════════════════════

async def enrich_assets(
    session: AsyncSession,
    asset_ids: list[str],
) -> EnrichResponse:
    """
    For each asset ID, invoke the LLM to classify its environment,
    category, and criticality, and return enriched metadata.
    """
    llm = get_llm()
    enriched: list[EnrichedAsset] = []

    for aid in asset_ids:
        try:
            asset = await get_asset_by_id(session, UUID(aid))
        except (ValueError, Exception) as exc:
            logger.warning("Invalid asset ID '%s': %s", aid, exc)
            enriched.append(EnrichedAsset(
                asset_id=aid,
                environment="unknown",
                category="unknown",
                criticality="low",
                enriched_metadata={},
                reasoning=f"Could not fetch asset: {exc}",
            ))
            continue

        if not asset:
            enriched.append(EnrichedAsset(
                asset_id=aid,
                environment="unknown",
                category="unknown",
                criticality="low",
                enriched_metadata={},
                reasoning="Asset not found in database.",
            ))
            continue

        # ── Build per-asset prompt ────────────────────
        prompt = ENRICHMENT_PROMPT.format(
            asset_id=str(asset.id),
            asset_type=asset.type,
            asset_value=asset.value,
            asset_status=asset.status,
            asset_tags=json.dumps(asset.tags or []),
            asset_metadata=json.dumps(asset.metadata_ or {}, default=str),
        )

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw_output = response.content.strip()

        logger.info("LLM Enrichment output for %s:\n%s", asset.value, raw_output)

        try:
            parsed = _safe_parse_json(raw_output)
            enriched.append(EnrichedAsset(
                asset_id=str(asset.id),
                environment=parsed.get("environment", "unknown"),
                category=parsed.get("category", "unknown"),
                criticality=parsed.get("criticality", "low"),
                enriched_metadata=parsed.get("enriched_metadata", {}),
                reasoning=parsed.get("reasoning", ""),
            ))
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Failed to parse enrichment JSON for %s: %s", aid, exc)
            enriched.append(EnrichedAsset(
                asset_id=str(asset.id),
                environment="unknown",
                category="unknown",
                criticality="low",
                enriched_metadata={},
                reasoning=f"LLM response could not be parsed: {exc}",
            ))

    return EnrichResponse(
        enriched_assets=enriched,
        total_processed=len(enriched),
    )


# ═══════════════════════════════════════════════════════
#  4) Natural-Language Report Generation
# ═══════════════════════════════════════════════════════

def _parse_scope_filters(scope: str) -> dict[str, str | None]:
    """
    Parse a scope string like 'type=subdomain' or 'tag=production'
    into filter kwargs for get_all_assets_raw.
    """
    filters: dict[str, str | None] = {
        "asset_type": None,
        "status": None,
        "tag": None,
    }
    if scope == "all":
        return filters

    for part in scope.split(","):
        part = part.strip()
        if "=" in part:
            key, val = part.split("=", 1)
            key = key.strip().lower()
            val = val.strip()
            if key == "type":
                filters["asset_type"] = val
            elif key == "status":
                filters["status"] = val
            elif key == "tag":
                filters["tag"] = val
    return filters


async def generate_report(
    session: AsyncSession,
    scope: str = "all",
    report_type: str = "inventory",
) -> ReportResponse:
    """
    Fetch assets matching the scope, build statistics, and invoke
    the LLM to produce a professional markdown report.
    """
    # ── Fetch assets ──────────────────────────────────
    filters = _parse_scope_filters(scope)
    assets = await get_all_assets_raw(
        session,
        asset_type=filters["asset_type"],
        status=filters["status"],
        tag=filters["tag"],
    )

    if not assets:
        return ReportResponse(
            report_type=report_type,
            scope=scope,
            report="# No Data Available\n\nNo assets match the specified scope. Import assets first.",
            generated_from_assets=0,
        )

    # ── Build statistics ──────────────────────────────
    type_counts = Counter(a.type for a in assets)
    status_counts = Counter(a.status for a in assets)

    type_breakdown = ", ".join(f"{t}: {c}" for t, c in type_counts.most_common())
    status_breakdown = ", ".join(f"{s}: {c}" for s, c in status_counts.most_common())

    asset_data = _serialize_assets_for_prompt(assets)

    # ── Build prompt ──────────────────────────────────
    prompt = REPORT_GENERATION_PROMPT.format(
        report_type=report_type,
        scope=scope,
        total_assets=len(assets),
        type_breakdown=type_breakdown,
        status_breakdown=status_breakdown,
        asset_data=asset_data,
    )

    llm = get_llm()
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    report_text = response.content.strip()

    logger.info("LLM Report generated — %d chars", len(report_text))

    return ReportResponse(
        report_type=report_type,
        scope=scope,
        report=report_text,
        generated_from_assets=len(assets),
    )
