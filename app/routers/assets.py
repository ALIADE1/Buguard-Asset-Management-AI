"""
REST API router for asset CRUD operations and bulk import.

Endpoints:
  POST /api/v1/assets/import     — Bulk import (idempotent upsert)
  GET  /api/v1/assets/           — Paginated list with filters
  GET  /api/v1/assets/{asset_id} — Single asset detail with relationships
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.asset import (
    AssetImportItem,
    AssetResponse,
    BulkImportRequest,
    BulkImportResponse,
    ImportRecordResult,
    PaginatedResponse,
)
from app.services.import_service import bulk_import_assets
from app.services.asset_service import get_asset_by_id, list_assets

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════
#  POST /import — Bulk Import
# ═══════════════════════════════════════════════════════

@router.post(
    "/import",
    response_model=BulkImportResponse,
    status_code=200,
    summary="Bulk-import assets (idempotent)",
    description=(
        "Accepts a JSON array of asset records. Re-importing the same "
        "asset updates `last_seen`, merges tags & metadata, and reactivates "
        "stale assets.  Malformed records are skipped gracefully."
    ),
)
async def import_assets(
    payload: BulkImportRequest,
    db: AsyncSession = Depends(get_db),
) -> BulkImportResponse:
    return await bulk_import_assets(db, payload.assets)


@router.post(
    "/import/raw",
    response_model=BulkImportResponse,
    status_code=200,
    summary="Bulk-import assets from a raw JSON array (lenient parsing)",
    description=(
        "Accepts a raw JSON array where each element is individually "
        "validated. Malformed records are collected as errors without "
        "crashing the batch."
    ),
)
async def import_assets_raw(
    payload: list[dict],
    db: AsyncSession = Depends(get_db),
) -> BulkImportResponse:
    """
    Lenient import endpoint — validates each record individually so a
    single bad record doesn't reject the entire request body.
    """
    valid_items: list[AssetImportItem] = []
    error_results: list[ImportRecordResult] = []

    for idx, raw in enumerate(payload):
        try:
            item = AssetImportItem.model_validate(raw)
            valid_items.append(item)
        except (ValidationError, Exception) as exc:
            val = raw.get("value", f"<record #{idx}>")
            logger.warning("Validation error for record #%d (%s): %s", idx, val, exc)
            error_results.append(ImportRecordResult(
                value=str(val),
                status="error",
                detail=str(exc),
            ))

    if not valid_items and error_results:
        return BulkImportResponse(
            total_submitted=len(payload),
            created=0,
            updated=0,
            errors=len(error_results),
            results=error_results,
        )

    if valid_items:
        result = await bulk_import_assets(db, valid_items)
        # Merge schema-level errors with service-level results
        result.errors += len(error_results)
        result.total_submitted = len(payload)
        result.results.extend(error_results)
        return result

    return BulkImportResponse(
        total_submitted=0,
        created=0,
        updated=0,
        errors=0,
        results=[],
    )


# ═══════════════════════════════════════════════════════
#  GET / — Paginated asset list
# ═══════════════════════════════════════════════════════

@router.get(
    "/",
    response_model=PaginatedResponse,
    summary="List assets with pagination & filters",
)
async def list_assets_endpoint(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(
        settings.api_default_page_size,
        ge=1,
        le=settings.api_max_page_size,
        description="Items per page",
    ),
    type: str | None = Query(None, description="Filter by asset type"),
    status: str | None = Query(None, description="Filter by status"),
    source: str | None = Query(None, description="Filter by source"),
    tag: str | None = Query(None, description="Filter by tag (array contains)"),
    search: str | None = Query(None, description="Case-insensitive search on value"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse:
    return await list_assets(
        db,
        page=page,
        page_size=page_size,
        asset_type=type,
        status=status,
        source=source,
        tag=tag,
        search=search,
    )


# ═══════════════════════════════════════════════════════
#  GET /{asset_id} — Single asset detail
# ═══════════════════════════════════════════════════════

@router.get(
    "/{asset_id}",
    response_model=AssetResponse,
    summary="Get asset details by ID",
)
async def get_asset(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> AssetResponse:
    asset = await get_asset_by_id(db, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return AssetResponse.model_validate(asset)
