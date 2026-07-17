"""Spell effects that actually reach the table: give ActiveEffect a kind, a subject,
and a place in the world.

Until now an ActiveEffect could only say "this character is maintaining something".
It could not say WHAT (so Guidance's 1d4 had nowhere to live), WHO it acts on (so
Guidance cast on someone else recorded nobody), or WHERE it exists (so an illusion
could not be seen by the NPCs standing next to it).

All four columns are additive and nullable/defaulted, so every existing row — rage,
wild shape, bare concentration — keeps its exact current meaning with kind="" and no
subject.

Revision ID: 20260722_spell_effects
Revises: 20260721_consequences
"""
from alembic import op
import sqlalchemy as sa

revision = "20260722_spell_effects"
down_revision = "20260721_consequences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("active_effects", sa.Column(
        "kind", sa.String(24), nullable=False, server_default=""))
    op.add_column("active_effects", sa.Column(
        "subject_ref", sa.String(64), nullable=True))
    op.add_column("active_effects", sa.Column(
        "scene_id", sa.String(32), nullable=True))
    op.add_column("active_effects", sa.Column(
        "location_id", sa.String(32), nullable=True))

    # The roll path asks "what effects act on this subject right now?" on every
    # eligible check, and the observer model asks "what world effects are here?".
    # Both are hot lookups; neither should scan.
    op.create_index("ix_active_effects_kind", "active_effects", ["kind"])
    op.create_index("ix_active_effects_subject_ref", "active_effects", ["subject_ref"])
    op.create_index("ix_active_effects_scene_id", "active_effects", ["scene_id"])
    op.create_index("ix_active_effects_location_id", "active_effects", ["location_id"])


def downgrade() -> None:
    op.drop_index("ix_active_effects_location_id", table_name="active_effects")
    op.drop_index("ix_active_effects_scene_id", table_name="active_effects")
    op.drop_index("ix_active_effects_subject_ref", table_name="active_effects")
    op.drop_index("ix_active_effects_kind", table_name="active_effects")
    op.drop_column("active_effects", "location_id")
    op.drop_column("active_effects", "scene_id")
    op.drop_column("active_effects", "subject_ref")
    op.drop_column("active_effects", "kind")
