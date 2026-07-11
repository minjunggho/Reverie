"""Campaign + CampaignMember. Canonical."""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, fk_id, pk_column
from app.models.enums import AssistanceLevel, CampaignStatus, MemberRole


def default_campaign_config() -> dict[str, Any]:
    """Owner-tunable campaign configuration (the human/owner override layer)."""
    return {
        "tone": "grounded-heroic",
        "assistance_default": AssistanceLevel.BEGINNER.value,
        "lethality": "standard",
        # How aggressively failures are turned into FAILURE_WITH_PROGRESS.
        # 'off' | 'moderate' (default) | 'aggressive'. Never auto-converts every miss.
        "failure_progress_level": "moderate",
        # Not every rest is punished by world pressure.
        "punish_every_rest": False,
        # Dice ritual: PLAYER_CLICK (player taps 🎲 for visible checks) | AUTO.
        "dice_mode": "PLAYER_CLICK",
        # Who owns objective world facts: AUTHORITATIVE_WORLD (default) | COLLABORATIVE.
        "world_mode": "AUTHORITATIVE_WORLD",
        # Supported-rules-subset flags (documented in app/tabletop/rules).
        "rules_subset": {
            "ability_checks": True,
            "saving_throws": True,
            "basic_attacks": True,
            "basic_combat": True,
        },
    }


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"

    id: Mapped[str] = pk_column()
    name: Mapped[str] = mapped_column(String(200))
    discord_guild_id: Mapped[str] = mapped_column(String(64), index=True)
    # One campaign per game channel — this is how a message resolves to a campaign.
    game_channel_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_user_id: Mapped[str] = fk_id("users.id")

    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=default_campaign_config)
    # In-world minutes since the campaign epoch. Engine-owned; never set by the LLM.
    current_game_time: Mapped[int] = mapped_column(Integer, default=0)

    # Campaign canon (E5). Player-safe brief + central question + Session 1 prep.
    # Deep lore lives in CampaignCanonRecord / Secret / NPC / Threat / Location.
    brief: Mapped[str] = mapped_column(Text, default="")
    central_question: Mapped[str] = mapped_column(Text, default="")
    session_prep: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=CampaignStatus.SETUP.value)
    # Monotonic event sequence counter for this campaign (assigned under lock).
    event_seq: Mapped[int] = mapped_column(Integer, default=0)


class CampaignMember(Base, TimestampMixin):
    __tablename__ = "campaign_members"
    __table_args__ = (
        UniqueConstraint("campaign_id", "user_id", name="uq_member_campaign_user"),
    )

    id: Mapped[str] = pk_column()
    campaign_id: Mapped[str] = fk_id("campaigns.id")
    user_id: Mapped[str] = fk_id("users.id")
    role: Mapped[str] = mapped_column(String(16), default=MemberRole.PLAYER.value)
    # MVP: exactly one active character per member. Nullable until a character exists.
    # (ForeignKey with SET NULL so deleting a character clears the pointer.)
    active_character_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("characters.id", ondelete="SET NULL", use_alter=True,
                   name="fk_member_active_character"),
        nullable=True,
    )
