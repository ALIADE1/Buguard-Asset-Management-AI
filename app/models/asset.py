"""
SQLAlchemy model for the `assets` table.

Design decisions
────────────────
• **Primary key** is a server-generated UUID (stable, collision-free).
• **Deduplication key** is the composite `(type, value)` — two assets are
  considered identical if they have the same type and canonical value.
  A unique index enforces this at the DB level.
• **Tags** are stored as a native PostgreSQL ARRAY(Text) column — fast to
  query with `@>` (contains) and avoids a separate join table for a
  simple list of labels.
• **Metadata** is a JSONB column so each asset type can store type-specific
  fields (cert expiry, tech version, ports, …) without schema bloat.
• **Timestamps** use timezone-aware UTC by default.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SAEnum,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base

# ── Enum values ────────────────────────────────────────

ASSET_TYPES = (
    "domain",
    "subdomain",
    "ip_address",
    "service",
    "certificate",
    "technology",
)

ASSET_STATUSES = ("active", "stale", "archived")

ASSET_SOURCES = ("import", "scan", "manual")


class Asset(Base):
    """Core asset entity in the Attack Surface inventory."""

    __tablename__ = "assets"

    # ── Primary key ────────────────────────────────────
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # ── Core fields ────────────────────────────────────
    type = Column(
        SAEnum(*ASSET_TYPES, name="asset_type_enum", create_constraint=True),
        nullable=False,
        index=True,
    )
    value = Column(String(2048), nullable=False, index=True)
    status = Column(
        SAEnum(*ASSET_STATUSES, name="asset_status_enum", create_constraint=True),
        nullable=False,
        default="active",
        server_default="active",
    )

    # ── Provenance ─────────────────────────────────────
    first_seen = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_seen = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    source = Column(
        SAEnum(*ASSET_SOURCES, name="asset_source_enum", create_constraint=True),
        nullable=False,
        default="import",
        server_default="import",
    )

    # ── Flexible fields ────────────────────────────────
    tags = Column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    asset_metadata = Column(
        "metadata",
        JSONB,
        key="asset_metadata",
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # ── Relationships (ORM) ────────────────────────────
    # An asset can be on either side of an AssetRelationship.
    outgoing_relationships = relationship(
        "AssetRelationship",
        foreign_keys="AssetRelationship.source_asset_id",
        back_populates="source_asset",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    incoming_relationships = relationship(
        "AssetRelationship",
        foreign_keys="AssetRelationship.target_asset_id",
        back_populates="target_asset",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # ── Indexes ────────────────────────────────────────
    __table_args__ = (
        # Deduplication: (type, value) must be unique.
        Index("uq_asset_type_value", "type", "value", unique=True),
        # GIN index on tags for efficient @> (array-contains) queries.
        Index("ix_asset_tags", "tags", postgresql_using="gin"),
        # GIN index on metadata JSONB for flexible filtering.
        Index("ix_asset_metadata", asset_metadata, postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<Asset {self.type}:{self.value} [{self.status}]>"
