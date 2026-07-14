"""Open-world geography: localized location names + aliases + discovery state,
and connection provenance / traversal mode / discovery state.

Existing rows remain valid: localized names default to NULL (fall back to the
canonical `name`), aliases default to an empty list, and every existing location and
connection is KNOWN with provenance IMPORTED_EXPLICIT — the safe, non-hiding default.

Revision ID: 20260719_geography
Revises: 20260718_beliefs
"""
from alembic import op
import sqlalchemy as sa

revision = "20260719_geography"
down_revision = "20260718_beliefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Localized names + aliases + discovery on locations.
    op.add_column("locations", sa.Column("name_th", sa.String(160), nullable=True))
    op.add_column("locations", sa.Column("name_en", sa.String(160), nullable=True))
    op.add_column("locations", sa.Column(
        "aliases", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("locations", sa.Column(
        "discovery_state", sa.String(20), nullable=False, server_default="KNOWN"))

    # Provenance + traversal + discovery on connections.
    op.add_column("location_connections", sa.Column(
        "provenance", sa.String(24), nullable=False, server_default="IMPORTED_EXPLICIT"))
    op.add_column("location_connections", sa.Column(
        "traversal_mode", sa.String(20), nullable=False, server_default="walk"))
    op.add_column("location_connections", sa.Column(
        "discovery_state", sa.String(20), nullable=False, server_default="KNOWN"))


def downgrade() -> None:
    op.drop_column("location_connections", "discovery_state")
    op.drop_column("location_connections", "traversal_mode")
    op.drop_column("location_connections", "provenance")
    op.drop_column("locations", "discovery_state")
    op.drop_column("locations", "aliases")
    op.drop_column("locations", "name_en")
    op.drop_column("locations", "name_th")
