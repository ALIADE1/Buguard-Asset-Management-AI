"""
Asset query service — read operations with filtering and pagination.
"""

from __future__ import annotations

import math
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import Asset
from app.schemas.asset import AssetResponse, AssetSummaryResponse, PaginatedResponse


async def get_asset_by_id(
    session: AsyncSession,
    asset_id: UUID,
) -> Asset | None:
    """Fetch a single asset with its relationships eagerly loaded."""
    stmt = (
        select(Asset)
        .options(
            selectinload(Asset.outgoing_relationships),
            selectinload(Asset.incoming_relationships),
        )
        .where(Asset.id == asset_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_assets(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    asset_type: str | None = None,
    status: str | None = None,
    source: str | None = None,
    tag: str | None = None,
    search: str | None = None,
) -> PaginatedResponse:
    """
    Paginated asset listing with optional filters.

    Filters:
      • asset_type — exact match on the `type` enum.
      • status     — exact match on the `status` enum.
      • source     — exact match on the `source` enum.
      • tag        — assets whose `tags` array contains this value.
      • search     — case-insensitive LIKE on `value`.
    """
    # ── Base query ─────────────────────────────────────
    base = select(Asset)
    count_base = select(func.count(Asset.id))

    # ── Apply filters ──────────────────────────────────
    if asset_type:
        base = base.where(Asset.type == asset_type)
        count_base = count_base.where(Asset.type == asset_type)
    if status:
        base = base.where(Asset.status == status)
        count_base = count_base.where(Asset.status == status)
    if source:
        base = base.where(Asset.source == source)
        count_base = count_base.where(Asset.source == source)
    if tag:
        base = base.where(Asset.tags.any(tag))
        count_base = count_base.where(Asset.tags.any(tag))
    if search:
        pattern = f"%{search.lower()}%"
        base = base.where(Asset.value.ilike(pattern))
        count_base = count_base.where(Asset.value.ilike(pattern))

    # ── Count ──────────────────────────────────────────
    total_result = await session.execute(count_base)
    total: int = total_result.scalar_one()

    # ── Paginate ───────────────────────────────────────
    offset = (page - 1) * page_size
    stmt = (
        base
        .order_by(Asset.last_seen.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    assets = result.scalars().all()

    return PaginatedResponse(
        items=[AssetSummaryResponse.model_validate(a) for a in assets],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, math.ceil(total / page_size)),
    )


async def get_all_assets_raw(
    session: AsyncSession,
    *,
    asset_type: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    limit: int = 500,
) -> list[Asset]:
    """
    Fetch raw Asset ORM objects for use by the AI layer.

    Includes relationships eagerly loaded. Capped at `limit` rows
    to prevent unbounded result sets being fed into LLM prompts.
    """
    stmt = (
        select(Asset)
        .options(
            selectinload(Asset.outgoing_relationships),
            selectinload(Asset.incoming_relationships),
        )
    )
    if asset_type:
        stmt = stmt.where(Asset.type == asset_type)
    if status:
        stmt = stmt.where(Asset.status == status)
    if tag:
        stmt = stmt.where(Asset.tags.any(tag))

    stmt = stmt.order_by(Asset.last_seen.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())
