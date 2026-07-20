"""Shared decision windows — the new unit of resolution.

Two tables only; nothing existing changes behaviour until planning mode opens a window.
`decision_windows` is the per-round state machine + immutable freeze + resolved package;
`action_submissions` is one structured, revisioned intention per actor per window. The
unique constraints are load-bearing: one live window per (scene, round), and exactly one
submission per (window, actor) so submit/edit is an idempotent upsert rather than a pile
of duplicate rows.

Revision ID: 20260728_decision_windows
Revises: 20260727_npc_intentions
"""
from alembic import op
import sqlalchemy as sa

revision = "20260728_decision_windows"
down_revision = "20260727_npc_intentions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_windows",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(32), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(32), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_id", sa.String(32), nullable=True),
        sa.Column("round_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="NONCOMBAT"),
        sa.Column("phase", sa.String(24), nullable=False, server_default="AWAITING_ACTIONS"),
        sa.Column("required_actor_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("excused_actor_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("frozen_snapshot", sa.JSON(), nullable=True),
        sa.Column("round_package", sa.JSON(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scene_id", "round_id", name="uq_window_scene_round"),
    )
    op.create_index("ix_decision_windows_scene_id", "decision_windows", ["scene_id"])

    op.create_table(
        "action_submissions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("window_id", sa.String(32), sa.ForeignKey("decision_windows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("raw_player_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("dialogue", sa.Text(), nullable=False, server_default=""),
        sa.Column("movement_intent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("destination", sa.String(200), nullable=False, server_default=""),
        sa.Column("primary_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("action_target", sa.String(200), nullable=False, server_default=""),
        sa.Column("bonus_action", sa.String(200), nullable=False, server_default=""),
        sa.Column("bonus_target", sa.String(200), nullable=False, server_default=""),
        sa.Column("interaction", sa.String(200), nullable=False, server_default=""),
        sa.Column("reaction_intent", sa.String(200), nullable=False, server_default=""),
        sa.Column("condition", sa.String(400), nullable=False, server_default=""),
        sa.Column("fallback_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("fallback_target", sa.String(200), nullable=False, server_default=""),
        sa.Column("desired_tone", sa.String(80), nullable=False, server_default=""),
        sa.Column("declared_resource_use", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("required_rolls", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("visibility", sa.String(12), nullable=False, server_default="OPEN"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("validation_errors", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("window_id", "actor_id", name="uq_submission_window_actor"),
    )
    op.create_index("ix_action_submissions_actor_id", "action_submissions", ["actor_id"])


def downgrade() -> None:
    op.drop_index("ix_action_submissions_actor_id", table_name="action_submissions")
    op.drop_table("action_submissions")
    op.drop_index("ix_decision_windows_scene_id", table_name="decision_windows")
    op.drop_table("decision_windows")
