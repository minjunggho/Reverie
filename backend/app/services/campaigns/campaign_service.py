"""Campaign + membership + identity resolution."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.campaign import Campaign, CampaignMember, default_campaign_config
from app.models.enums import CampaignStatus, MemberRole
from app.models.user import User


class CampaignService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- users ---------------------------------------------------------------
    async def get_or_create_user(self, discord_user_id: str, display_name: str) -> User:
        user = (
            await self.session.execute(
                select(User).where(User.discord_user_id == discord_user_id)
            )
        ).scalar_one_or_none()
        if user is None:
            user = User(discord_user_id=discord_user_id, display_name=display_name)
            self.session.add(user)
            await self.session.flush()
        elif user.display_name != display_name:
            user.display_name = display_name
        return user

    # --- campaigns -----------------------------------------------------------
    async def create_campaign(
        self,
        *,
        name: str,
        discord_guild_id: str,
        game_channel_id: str,
        owner_discord_user_id: str,
        owner_display_name: str,
        config: dict[str, Any] | None = None,
    ) -> Campaign:
        existing = await self.resolve_campaign_by_channel(game_channel_id)
        if existing is not None:
            raise ConflictError(f"a campaign already exists for channel {game_channel_id}")

        owner = await self.get_or_create_user(owner_discord_user_id, owner_display_name)
        campaign = Campaign(
            name=name,
            discord_guild_id=discord_guild_id,
            game_channel_id=game_channel_id,
            owner_user_id=owner.id,
            config=config or default_campaign_config(),
            status=CampaignStatus.SETUP.value,
        )
        self.session.add(campaign)
        await self.session.flush()

        # The owner is also a member (role OWNER).
        await self.add_member(
            campaign_id=campaign.id,
            discord_user_id=owner_discord_user_id,
            display_name=owner_display_name,
            role=MemberRole.OWNER,
        )
        return campaign

    async def resolve_campaign_by_channel(self, game_channel_id: str) -> Campaign | None:
        return (
            await self.session.execute(
                select(Campaign).where(Campaign.game_channel_id == game_channel_id)
            )
        ).scalar_one_or_none()

    async def get_campaign(self, campaign_id: str) -> Campaign:
        campaign = await self.session.get(Campaign, campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign {campaign_id} not found")
        return campaign

    async def activate_campaign(self, campaign_id: str) -> Campaign:
        campaign = await self.get_campaign(campaign_id)
        campaign.status = CampaignStatus.ACTIVE.value
        return campaign

    # --- membership ----------------------------------------------------------
    async def add_member(
        self,
        *,
        campaign_id: str,
        discord_user_id: str,
        display_name: str,
        role: MemberRole = MemberRole.PLAYER,
    ) -> CampaignMember:
        user = await self.get_or_create_user(discord_user_id, display_name)
        existing = await self.resolve_member(campaign_id, discord_user_id)
        if existing is not None:
            return existing  # idempotent
        member = CampaignMember(campaign_id=campaign_id, user_id=user.id, role=role.value)
        self.session.add(member)
        await self.session.flush()
        return member

    async def resolve_member(
        self, campaign_id: str, discord_user_id: str
    ) -> CampaignMember | None:
        """Resolve a Discord user to their CampaignMember in this campaign."""
        return (
            await self.session.execute(
                select(CampaignMember)
                .join(User, User.id == CampaignMember.user_id)
                .where(
                    CampaignMember.campaign_id == campaign_id,
                    User.discord_user_id == discord_user_id,
                )
            )
        ).scalar_one_or_none()

    async def get_member(self, member_id: str) -> CampaignMember:
        member = await self.session.get(CampaignMember, member_id)
        if member is None:
            raise NotFoundError(f"member {member_id} not found")
        return member

    async def list_members(self, campaign_id: str) -> list[CampaignMember]:
        return list(
            (
                await self.session.execute(
                    select(CampaignMember).where(CampaignMember.campaign_id == campaign_id)
                )
            ).scalars()
        )
