"""
Pydantic schemas for the Asset domain.

Organized into:
  • *Input* schemas   — what the API accepts  (Create / Import / Update).
  • *Output* schemas  — what the API returns   (AssetResponse, paginated wrappers).
  • *Enum* helpers     — shared across input/output.

Every schema uses `model_config = ConfigDict(from_attributes=True)` so that
SQLAlchemy model instances can be serialised directly.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ═══════════════════════════════════════════════════════
#  Enums
# ═══════════════════════════════════════════════════════

class AssetType(str, enum.Enum):
    domain = "domain"
    subdomain = "subdomain"
    ip_address = "ip_address"
    service = "service"
    certificate = "certificate"
    technology = "technology"


class AssetStatus(str, enum.Enum):
    active = "active"
    stale = "stale"
    archived = "archived"


class AssetSource(str, enum.Enum):
    import_ = "import"
    scan = "scan"
    manual = "manual"

    # Allow `"import"` (reserved keyword) from JSON payloads
    @classmethod
    def _missing_(cls, value: object):
        for member in cls:
            if member.value == value:
                return member
        return None


# ═══════════════════════════════════════════════════════
#  Input schemas
# ═══════════════════════════════════════════════════════

class AssetImportItem(BaseModel):
    """
    A single asset record inside a bulk-import payload.

    Fields like `parent`, `covers`, `detected_on` express relationships
    by referring to the *value* of another asset in the same batch (or an
    existing asset in the DB).  The import service resolves these into
    rows in the `asset_relationships` table.
    """

    type: AssetType
    value: str = Field(..., min_length=1, max_length=2048)
    status: AssetStatus = AssetStatus.active
    source: AssetSource = AssetSource.import_
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ── Relationship hints (optional) ──────────────────
    parent: str | None = Field(
        default=None,
        description="Value of the parent asset (e.g. domain for a subdomain).",
    )
    covers: list[str] = Field(
        default_factory=list,
        description="Values of assets this certificate covers.",
    )
    detected_on: str | None = Field(
        default=None,
        description="Value of the asset this technology was detected on.",
    )
    runs_on: str | None = Field(
        default=None,
        description="Value of the IP address this service runs on.",
    )
    resolves_to: list[str] = Field(
        default_factory=list,
        description="IP addresses this subdomain resolves to.",
    )

    @field_validator("value")
    @classmethod
    def normalise_value(cls, v: str) -> str:
        """Lower-case and strip whitespace so deduplication is stable."""
        return v.strip().lower()

    model_config = ConfigDict(use_enum_values=True)


class BulkImportRequest(BaseModel):
    """Top-level payload for the bulk import endpoint."""

    assets: list[AssetImportItem] = Field(..., min_length=1, max_length=10_000)


# ═══════════════════════════════════════════════════════
#  Output schemas
# ═══════════════════════════════════════════════════════

class RelationshipResponse(BaseModel):
    """Serialized view of one edge in the asset graph."""

    id: UUID
    source_asset_id: UUID
    target_asset_id: UUID
    relationship_type: str
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="metadata_",
        serialization_alias="metadata",
    )
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AssetResponse(BaseModel):
    """Full asset representation returned by the API."""

    id: UUID
    type: AssetType
    value: str
    status: AssetStatus
    first_seen: datetime
    last_seen: datetime
    source: AssetSource
    tags: list[str]
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="asset_metadata",
        serialization_alias="metadata",
    )

    # Related edges (kept lightweight — just IDs and types)
    outgoing_relationships: list[RelationshipResponse] = Field(default_factory=list)
    incoming_relationships: list[RelationshipResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AssetSummaryResponse(BaseModel):
    """Compact asset view used in list / paginated endpoints."""

    id: UUID
    type: AssetType
    value: str
    status: AssetStatus
    first_seen: datetime
    last_seen: datetime
    source: AssetSource
    tags: list[str]

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ═══════════════════════════════════════════════════════
#  Pagination wrapper
# ═══════════════════════════════════════════════════════

class PaginatedResponse(BaseModel):
    """Generic paginated container."""

    items: list[AssetSummaryResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# ═══════════════════════════════════════════════════════
#  Bulk import result
# ═══════════════════════════════════════════════════════

class ImportRecordResult(BaseModel):
    """Per-record outcome inside a bulk import."""

    value: str
    status: str  # "created" | "updated" | "error"
    detail: str | None = None


class BulkImportResponse(BaseModel):
    """Summary of a bulk import operation."""

    total_submitted: int
    created: int
    updated: int
    errors: int
    results: list[ImportRecordResult]
