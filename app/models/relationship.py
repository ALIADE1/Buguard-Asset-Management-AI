"""
SQLAlchemy model for the `asset_relationships` table.

Design decisions
────────────────
• Modelled as a **directed adjacency table** — every edge goes from a
  *source* asset to a *target* asset with a typed label (`relationship_type`).
• Bidirectional links (e.g. ip ↔ subdomain) are stored as **two rows** with
  complementary types.  This keeps queries simple: "give me everything this
  asset points to" is a single `WHERE source_asset_id = ?`.
• A unique constraint on `(source_asset_id, target_asset_id,
  relationship_type)` prevents duplicate edges.
• An optional JSONB `metadata` column stores edge-specific data (e.g.
  port/protocol for a service → ip relationship).

Relationship type vocabulary
────────────────────────────
  subdomain  ─belongs_to─▸  domain
  ip_address ─resolves_to─▸ subdomain   (and reverse)
  service    ─runs_on─────▸ ip_address
  certificate─covers──────▸ domain / subdomain
  technology ─detected_on─▸ subdomain / service
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class AssetRelationship(Base):
    """Directed edge between two assets in the relationship graph."""

    __tablename__ = "asset_relationships"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    source_asset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_asset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )

    relationship_type = Column(
        String(64),
        nullable=False,
        doc="Semantic label: belongs_to, resolves_to, runs_on, covers, detected_on, …",
    )

    metadata_ = Column("metadata", JSONB, nullable=False, default=dict, server_default="{}")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── ORM back-refs ──────────────────────────────────
    source_asset = relationship(
        "Asset",
        foreign_keys=[source_asset_id],
        back_populates="outgoing_relationships",
    )
    target_asset = relationship(
        "Asset",
        foreign_keys=[target_asset_id],
        back_populates="incoming_relationships",
    )

    # ── Constraints & indexes ──────────────────────────
    __table_args__ = (
        Index(
            "uq_relationship_edge",
            "source_asset_id",
            "target_asset_id",
            "relationship_type",
            unique=True,
        ),
        Index("ix_rel_source", "source_asset_id"),
        Index("ix_rel_target", "target_asset_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<AssetRelationship {self.source_asset_id} "
            f"─{self.relationship_type}─▸ {self.target_asset_id}>"
        )
