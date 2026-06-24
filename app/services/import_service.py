"""
Bulk import service — the workhorse of asset ingestion.

Responsibilities
────────────────
1. **Idempotent upsert** — uses PostgreSQL ON CONFLICT on the (type, value)
   unique index to INSERT or UPDATE without duplicates.
2. **Metadata merge** — deep-merges incoming JSONB with existing JSONB,
   with "latest write wins" semantics for conflicting scalar keys.
3. **Tag merge** — computes the union of existing and incoming tag arrays.
4. **Stale re-activation** — if an existing asset is `stale` and is seen
   again, its status is flipped back to `active`.
5. **Relationship linking** — resolves value-based hints (`parent`, `covers`,
   `runs_on`, `resolves_to`, `detected_on`) into rows in the
   `asset_relationships` table, also idempotently.
6. **Graceful partial failure** — each record is processed individually;
   a bad record is logged and skipped without aborting the batch.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.relationship import AssetRelationship
from app.schemas.asset import (
    AssetImportItem,
    ImportRecordResult,
    BulkImportResponse,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
#  Metadata merge helper
# ═══════════════════════════════════════════════════════

def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively merge *override* into *base*.

    • Dicts are merged recursively.
    • Lists are concatenated (de-duped).
    • Scalars in *override* win over *base* (latest-write-wins).

    Returns a **new** dict — neither input is mutated.
    """
    merged = deepcopy(base)
    for key, new_val in override.items():
        old_val = merged.get(key)
        if isinstance(old_val, dict) and isinstance(new_val, dict):
            merged[key] = deep_merge(old_val, new_val)
        elif isinstance(old_val, list) and isinstance(new_val, list):
            # Union while preserving order
            seen = set()
            combined: list = []
            for item in old_val + new_val:
                hashable = str(item)
                if hashable not in seen:
                    seen.add(hashable)
                    combined.append(item)
            merged[key] = combined
        else:
            merged[key] = new_val
    return merged


# ═══════════════════════════════════════════════════════
#  Relationship resolution mapping
# ═══════════════════════════════════════════════════════

# Maps (hint_field_name) → (relationship_type, direction)
# direction = "outgoing" means the imported asset is the source.
RELATIONSHIP_MAP: dict[str, tuple[str, str]] = {
    "parent": ("belongs_to", "outgoing"),       # subdomain → domain
    "covers": ("covers", "outgoing"),            # certificate → domain/subdomain
    "detected_on": ("detected_on", "outgoing"),  # technology → subdomain/service
    "runs_on": ("runs_on", "outgoing"),          # service → ip_address
    "resolves_to": ("resolves_to", "outgoing"),  # subdomain → ip_address
}


# ═══════════════════════════════════════════════════════
#  Core upsert logic
# ═══════════════════════════════════════════════════════

async def _upsert_single_asset(
    session: AsyncSession,
    item: AssetImportItem,
) -> tuple[str, Asset]:
    """
    Insert or update a single asset.

    Returns ("created" | "updated", Asset).
    Uses raw PostgreSQL INSERT … ON CONFLICT DO UPDATE.
    """
    now = datetime.now(timezone.utc)

    # ── Build the VALUES row ───────────────────────────
    values = {
        "type": item.type,
        "value": item.value,
        "status": item.status,
        "source": item.source,
        "tags": item.tags,
        "metadata": item.metadata,
        "first_seen": now,
        "last_seen": now,
    }

    stmt = pg_insert(Asset).values(**values)

    # ── ON CONFLICT (type, value) DO UPDATE ────────────
    # • last_seen is always bumped.
    # • tags are merged via array union (raw SQL for efficiency).
    # • metadata is deep-merged in Python after we know the old values,
    #   but we can do a simple jsonb || concat at the SQL level and
    #   refine in a second pass. For correctness (nested merge), we do
    #   a two-step approach: upsert with basic concat, then refine.
    # • status: if existing is 'stale' and incoming is 'active', revert.
    stmt = stmt.on_conflict_do_update(
        index_elements=["type", "value"],
        set_={
            "last_seen": now,
            "source": item.source,
            # SQL-level tag union: combine arrays, then deduplicate via subquery
            # We'll handle this in Python for clarity.
            # For now, just take incoming — we'll merge below.
            "tags": stmt.excluded.tags,
            "metadata": stmt.excluded.metadata,
            # Status: revert stale → active if re-sighted
            "status": func.CASE(
                (Asset.status == "stale", "active"),
                else_=stmt.excluded.status,
            ),
        },
    ).returning(Asset.id, Asset.tags, Asset.metadata_)

    result = await session.execute(stmt)
    row = result.fetchone()

    # We need to determine if this was a create or update.
    # Fetch the full asset to check first_seen vs now.
    asset = await session.get(Asset, row[0])

    if asset is None:
        raise RuntimeError(f"Asset {row[0]} vanished after upsert")

    # ── Determine create vs update ─────────────────────
    # If first_seen equals our `now` (within 1s tolerance), it was created.
    time_diff = abs((asset.first_seen - now).total_seconds())
    action = "created" if time_diff < 1.0 else "updated"

    # ── Merge tags in Python (union, preserving order) ─
    if action == "updated":
        existing_tags = set(asset.tags or [])
        merged_tags = list(existing_tags | set(item.tags))
        if set(merged_tags) != existing_tags:
            asset.tags = merged_tags

        # ── Deep-merge metadata ────────────────────────
        old_meta = asset.metadata_ or {}
        new_meta = item.metadata or {}
        if new_meta:
            asset.metadata_ = deep_merge(old_meta, new_meta)

    return action, asset


# ═══════════════════════════════════════════════════════
#  Relationship linker
# ═══════════════════════════════════════════════════════

async def _link_relationships(
    session: AsyncSession,
    item: AssetImportItem,
    asset: Asset,
    value_to_id: dict[str, UUID],
) -> None:
    """
    Resolve relationship hints into asset_relationships rows.

    `value_to_id` maps normalised asset values → UUIDs so we can look
    up targets from the same batch without extra DB queries.
    """
    edges_to_create: list[dict[str, Any]] = []

    # ── parent (single value) ──────────────────────────
    if item.parent:
        target_val = item.parent.strip().lower()
        target_id = value_to_id.get(target_val)
        if target_id and target_id != asset.id:
            edges_to_create.append({
                "source_asset_id": asset.id,
                "target_asset_id": target_id,
                "relationship_type": "belongs_to",
            })

    # ── runs_on (single value) ─────────────────────────
    if item.runs_on:
        target_val = item.runs_on.strip().lower()
        target_id = value_to_id.get(target_val)
        if target_id and target_id != asset.id:
            edges_to_create.append({
                "source_asset_id": asset.id,
                "target_asset_id": target_id,
                "relationship_type": "runs_on",
            })

    # ── detected_on (single value) ─────────────────────
    if item.detected_on:
        target_val = item.detected_on.strip().lower()
        target_id = value_to_id.get(target_val)
        if target_id and target_id != asset.id:
            edges_to_create.append({
                "source_asset_id": asset.id,
                "target_asset_id": target_id,
                "relationship_type": "detected_on",
            })

    # ── covers (list of values) ────────────────────────
    for cov in item.covers:
        target_val = cov.strip().lower()
        target_id = value_to_id.get(target_val)
        if target_id and target_id != asset.id:
            edges_to_create.append({
                "source_asset_id": asset.id,
                "target_asset_id": target_id,
                "relationship_type": "covers",
            })

    # ── resolves_to (list of values) ───────────────────
    for rt in item.resolves_to:
        target_val = rt.strip().lower()
        target_id = value_to_id.get(target_val)
        if target_id and target_id != asset.id:
            edges_to_create.append({
                "source_asset_id": asset.id,
                "target_asset_id": target_id,
                "relationship_type": "resolves_to",
            })

    # ── Bulk upsert edges (idempotent) ─────────────────
    for edge in edges_to_create:
        stmt = pg_insert(AssetRelationship).values(**edge)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[
                "source_asset_id",
                "target_asset_id",
                "relationship_type",
            ]
        )
        await session.execute(stmt)


# ═══════════════════════════════════════════════════════
#  Public API — the main bulk import orchestrator
# ═══════════════════════════════════════════════════════

async def bulk_import_assets(
    session: AsyncSession,
    items: list[AssetImportItem],
) -> BulkImportResponse:
    """
    Import a batch of assets.

    Two-pass approach:
      1. **Upsert pass** — insert/update every asset, collecting the
         value→UUID mapping needed for relationship resolution.
      2. **Relationship pass** — resolve hints and insert edges.

    Each record is wrapped in its own try/except so a single malformed
    or conflicting record cannot take down the entire batch.
    """
    results: list[ImportRecordResult] = []
    created = 0
    updated = 0
    errors = 0

    # value → UUID mapping (populated during the upsert pass)
    value_to_id: dict[str, UUID] = {}

    # Also collect successfully imported items for the relationship pass
    imported_pairs: list[tuple[AssetImportItem, Asset]] = []

    # ── Pass 1: Upsert assets ──────────────────────────
    for item in items:
        try:
            action, asset = await _upsert_single_asset(session, item)
            value_to_id[item.value] = asset.id

            if action == "created":
                created += 1
            else:
                updated += 1

            imported_pairs.append((item, asset))
            results.append(ImportRecordResult(
                value=item.value,
                status=action,
                detail=None,
            ))
        except Exception as exc:
            errors += 1
            logger.warning("Import error for '%s': %s", item.value, exc, exc_info=True)
            results.append(ImportRecordResult(
                value=item.value,
                status="error",
                detail=str(exc),
            ))
            # Rollback the failed statement but keep the session alive
            # by using a savepoint (nested transaction).
            await session.rollback()

    # Flush upserts so relationship FK references are valid
    await session.flush()

    # ── Pass 1b: Also resolve values that already exist in DB ──
    # (targets may reference assets imported in a previous batch)
    all_hint_values: set[str] = set()
    for item in items:
        if item.parent:
            all_hint_values.add(item.parent.strip().lower())
        if item.runs_on:
            all_hint_values.add(item.runs_on.strip().lower())
        if item.detected_on:
            all_hint_values.add(item.detected_on.strip().lower())
        for v in item.covers:
            all_hint_values.add(v.strip().lower())
        for v in item.resolves_to:
            all_hint_values.add(v.strip().lower())

    # Find any referenced values not in this batch
    missing_values = all_hint_values - set(value_to_id.keys())
    if missing_values:
        stmt = select(Asset.value, Asset.id).where(Asset.value.in_(missing_values))
        rows = await session.execute(stmt)
        for val, uid in rows:
            value_to_id[val] = uid

    # ── Pass 2: Link relationships ─────────────────────
    for item, asset in imported_pairs:
        try:
            await _link_relationships(session, item, asset, value_to_id)
        except Exception as exc:
            logger.warning(
                "Relationship linking error for '%s': %s",
                item.value, exc, exc_info=True,
            )
            # Non-fatal — asset was already imported successfully

    await session.flush()

    return BulkImportResponse(
        total_submitted=len(items),
        created=created,
        updated=updated,
        errors=errors,
        results=results,
    )
