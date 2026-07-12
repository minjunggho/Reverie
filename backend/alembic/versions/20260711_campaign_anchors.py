"""Canonical campaign anchors: starting location, party anchor, default opening,
world model version. Creation order is never campaign intent again.

Revision ID: 20260711_anchors
Revises: 20260710_canon
"""
from alembic import op
import sqlalchemy as sa

revision = "20260711_anchors"
down_revision = "20260710_canon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("starting_location_id", sa.String(32), nullable=True))
    op.add_column("campaigns", sa.Column("current_party_anchor_id", sa.String(32), nullable=True))
    op.add_column("campaigns", sa.Column("default_session_opening", sa.Text(), nullable=False,
                                         server_default=""))
    op.add_column("campaigns", sa.Column("world_model_version", sa.Integer(), nullable=False,
                                         server_default="2"))
    # Backfill: existing imported campaigns already store the opening location in
    # session_prep.opening_location_id — promote it to the canonical field.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, session_prep FROM campaigns")).fetchall()
    import json
    for cid, prep in rows:
        if not prep:
            continue
        data = prep if isinstance(prep, dict) else json.loads(prep)
        opening = data.get("opening_location_id")
        if opening:
            conn.execute(sa.text(
                "UPDATE campaigns SET starting_location_id = :loc WHERE id = :cid"
            ), {"loc": opening, "cid": cid})


def downgrade() -> None:
    op.drop_column("campaigns", "world_model_version")
    op.drop_column("campaigns", "default_session_opening")
    op.drop_column("campaigns", "current_party_anchor_id")
    op.drop_column("campaigns", "starting_location_id")
