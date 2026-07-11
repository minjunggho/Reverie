"""Activity HTTP API (E6) — /api/activity/v1.

Routes authenticate (bearer Activity session), authorize (CampaignMember + role
resolved server-side from the VERIFIED Discord identity — never from a frontend
flag), validate, and call projection/domain services. No game logic lives here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.activity import (
    ActivityAuthError,
    ActivityPrincipal,
    DiscordOAuthClient,
    mint_session_token,
    resolve_secret,
    verify_session_token,
)
from app.core.config import get_settings
from app.db.session import get_database
from app.models.campaign import Campaign, CampaignMember
from app.models.enums import MemberRole
from app.models.location import Location
from app.models.user import User
from app.services.activity import grimoire, studio
from app.services.campaigns import CampaignService, CharacterService
from app.services.scenes import SceneService
from app.services.sessions.session_service import SessionService

router = APIRouter(prefix="/api/activity/v1", tags=["activity"])

# Injectable for tests (never patched in production).
_oauth_client_factory = DiscordOAuthClient


def set_oauth_client_factory(factory) -> None:  # test seam
    global _oauth_client_factory
    _oauth_client_factory = factory


# --- auth ---------------------------------------------------------------------
class ExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=512)


@router.get("/config")
async def activity_config() -> dict:
    """Public frontend bootstrap config. ONLY the public client id — never secrets."""
    settings = get_settings()
    return {"discord_client_id": settings.discord_client_id}


@router.post("/auth/exchange")
async def auth_exchange(body: ExchangeRequest) -> dict:
    settings = get_settings()
    try:
        identity = await _oauth_client_factory(settings).exchange_and_identify(body.code)
    except ActivityAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    db = get_database()
    async with db.unit_of_work() as s:
        user = await CampaignService(s).get_or_create_user(
            identity["id"], identity["display_name"])
        user_id, discord_id, display = user.id, user.discord_user_id, user.display_name

    token = mint_session_token(
        resolve_secret(settings), user_id=user_id, discord_user_id=discord_id,
        display_name=display, ttl_minutes=settings.activity_session_ttl_minutes,
    )
    return {
        "session_token": token,
        "expires_in": settings.activity_session_ttl_minutes * 60,
        # Passed back to the Embedded App SDK's authenticate(); Reverie doesn't keep it.
        "discord_access_token": identity["access_token"],
        "user": {"discord_user_id": discord_id, "display_name": display},
    }


def principal_from_header(authorization: str | None = Header(default=None)) -> ActivityPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Activity session")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return verify_session_token(resolve_secret(get_settings()), token)
    except ActivityAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


async def _member_or_403(s, campaign_id: str, principal: ActivityPrincipal
                         ) -> tuple[Campaign, CampaignMember]:
    campaign = await s.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    member = await CampaignService(s).resolve_member(campaign_id, principal.discord_user_id)
    if member is None:
        raise HTTPException(status_code=403, detail="not a member of this campaign")
    return campaign, member


def _require_owner(member: CampaignMember) -> None:
    if member.role != MemberRole.OWNER.value:
        raise HTTPException(status_code=403, detail="DM Studio requires the campaign owner")


# --- context --------------------------------------------------------------------
@router.get("/context")
async def activity_context(
    channel_id: str | None = Query(default=None),
    guild_id: str | None = Query(default=None),
    principal: ActivityPrincipal = Depends(principal_from_header),
) -> dict:
    db = get_database()
    async with db.session() as s:
        campaign = None
        if channel_id:
            campaign = await CampaignService(s).resolve_campaign_by_channel(channel_id)
            if campaign is not None and guild_id and campaign.discord_guild_id != guild_id:
                campaign = None  # forged/mismatched context — treat as unbound

        # Campaigns this user belongs to (never a global list).
        user = (await s.execute(select(User).where(
            User.discord_user_id == principal.discord_user_id))).scalar_one_or_none()
        my_campaigns: list[dict] = []
        if user is not None:
            memberships = list((await s.execute(select(CampaignMember).where(
                CampaignMember.user_id == user.id))).scalars())
            for m in memberships:
                c = await s.get(Campaign, m.campaign_id)
                if c is not None:
                    my_campaigns.append({"id": c.id, "name": c.name, "role": m.role})

        member = None
        if campaign is not None:
            member = await CampaignService(s).resolve_member(
                campaign.id, principal.discord_user_id)
            if member is None:
                campaign = None  # bound channel, but this user isn't at that table

        character = session_info = scene_info = None
        if campaign is not None and member is not None:
            char = await CharacterService(s).get_active_character(member)
            if char is not None:
                character = {"id": char.id, "name": char.name,
                             "char_class": char.char_class, "level": char.level}
            active = await SessionService(s).get_active_session(campaign.id)
            if active is not None:
                session_info = {"id": active.id, "number": active.number,
                                "status": active.status, "active": True}
                scene = await SceneService(s).get_active_scene(active.id)
                if scene is not None:
                    loc = await s.get(Location, scene.location_id) if scene.location_id else None
                    scene_info = {"id": scene.id,
                                  "location_name": loc.name if loc else None}

        return {
            "user": {"discord_user_id": principal.discord_user_id,
                     "display_name": principal.display_name},
            "campaign": ({"id": campaign.id, "name": campaign.name}
                         if campaign is not None else None),
            "membership": ({"role": member.role,
                            "can_open_dm_studio": member.role == MemberRole.OWNER.value}
                           if member is not None else None),
            "character": character,
            "session": session_info,
            "scene": scene_info,
            "my_campaigns": my_campaigns,
        }


# --- player Grimoire --------------------------------------------------------------
async def _char_or_404(s, member: CampaignMember):
    char = await CharacterService(s).get_active_character(member)
    if char is None:
        raise HTTPException(status_code=404, detail="no active character")
    return char


@router.get("/campaigns/{campaign_id}/grimoire/overview")
async def grimoire_overview(campaign_id: str,
                            principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        char = await _char_or_404(s, member)
        return await grimoire.build_overview(s, character=char, campaign=campaign)


@router.get("/campaigns/{campaign_id}/grimoire/skills")
async def grimoire_skills(campaign_id: str,
                          principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        _, member = await _member_or_403(s, campaign_id, principal)
        char = await _char_or_404(s, member)
        return grimoire.build_abilities_and_skills(s, character=char)


@router.get("/campaigns/{campaign_id}/grimoire/spellbook")
async def grimoire_spellbook(campaign_id: str,
                             principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        _, member = await _member_or_403(s, campaign_id, principal)
        char = await _char_or_404(s, member)
        return await grimoire.build_spellbook(s, character=char)


@router.get("/campaigns/{campaign_id}/grimoire/features")
async def grimoire_features(campaign_id: str,
                            principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        _, member = await _member_or_403(s, campaign_id, principal)
        char = await _char_or_404(s, member)
        return await grimoire.build_features(s, character=char)


@router.get("/campaigns/{campaign_id}/grimoire/inventory")
async def grimoire_inventory(campaign_id: str,
                             principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        _, member = await _member_or_403(s, campaign_id, principal)
        char = await _char_or_404(s, member)
        return await grimoire.build_inventory(s, character=char)


@router.get("/campaigns/{campaign_id}/grimoire/story")
async def grimoire_story(campaign_id: str,
                         principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        char = await _char_or_404(s, member)
        return await grimoire.build_story(s, character=char, campaign=campaign)


@router.get("/campaigns/{campaign_id}/grimoire/party")
async def grimoire_party(campaign_id: str,
                         principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        return await grimoire.build_party(s, campaign=campaign, viewer_member=member)


@router.get("/campaigns/{campaign_id}/grimoire/chronicle")
async def grimoire_chronicle(
    campaign_id: str, limit: int = Query(default=40, ge=1, le=100),
    before_seq: int | None = Query(default=None),
    principal: ActivityPrincipal = Depends(principal_from_header),
) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        char = await _char_or_404(s, member)
        return await grimoire.build_chronicle(
            s, campaign=campaign, character_id=char.id, limit=limit, before_seq=before_seq)


# --- DM Studio ---------------------------------------------------------------------
@router.get("/campaigns/{campaign_id}/studio/command-center")
async def studio_command_center(campaign_id: str,
                                principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        return await studio.build_command_center(s, campaign=campaign)


@router.get("/campaigns/{campaign_id}/studio/scene")
async def studio_scene(campaign_id: str,
                       principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        return await studio.build_current_scene(s, campaign=campaign)


@router.get("/campaigns/{campaign_id}/studio/world")
async def studio_world(campaign_id: str,
                       principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        return await studio.build_world(s, campaign=campaign)


@router.get("/campaigns/{campaign_id}/studio/npcs")
async def studio_npcs(campaign_id: str,
                      principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        return await studio.build_npcs(s, campaign=campaign)


@router.get("/campaigns/{campaign_id}/studio/npcs/{npc_id}")
async def studio_npc_detail(campaign_id: str, npc_id: str,
                            principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        detail = await studio.build_npc_detail(s, campaign=campaign, npc_id=npc_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="npc not found")
        return detail


@router.get("/campaigns/{campaign_id}/studio/threats")
async def studio_threats(campaign_id: str,
                         principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        return await studio.build_threats(s, campaign=campaign)


@router.get("/campaigns/{campaign_id}/studio/secrets")
async def studio_secrets(campaign_id: str,
                         principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        return await studio.build_secrets(s, campaign=campaign)


@router.get("/campaigns/{campaign_id}/studio/events")
async def studio_events(
    campaign_id: str, limit: int = Query(default=50, ge=1, le=200),
    before_seq: int | None = Query(default=None),
    visibility: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    principal: ActivityPrincipal = Depends(principal_from_header),
) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        return await studio.build_events(
            s, campaign=campaign, limit=limit, before_seq=before_seq,
            visibility=visibility, event_type=event_type)


@router.get("/campaigns/{campaign_id}/studio/imports")
async def studio_imports(campaign_id: str,
                         principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    async with get_database().session() as s:
        campaign, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        return await studio.build_imports(s, campaign=campaign)


# --- DM Studio mutations (existing validated domain operations ONLY) ---------------
@router.post("/campaigns/{campaign_id}/studio/imports/{import_id}/approve")
async def studio_import_approve(campaign_id: str, import_id: str,
                                principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    from app.core.errors import ConflictError, NotFoundError, ValidationError
    from app.services.campaigns.canon_import import CanonImportService

    db = get_database()
    async with db.unit_of_work() as s:
        _, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        try:
            review = await CanonImportService(s).approve(
                import_id=import_id, campaign_id=campaign_id)
        except (ConflictError, ValidationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "APPROVED", "counts": review.counts, "warnings": review.warnings}


@router.post("/campaigns/{campaign_id}/studio/imports/{import_id}/reject")
async def studio_import_reject(campaign_id: str, import_id: str,
                               principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    from app.core.errors import ConflictError, NotFoundError
    from app.services.campaigns.canon_import import CanonImportService

    db = get_database()
    async with db.unit_of_work() as s:
        _, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        try:
            await CanonImportService(s).reject(import_id=import_id, campaign_id=campaign_id)
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "REJECTED"}


@router.post("/campaigns/{campaign_id}/studio/imports/{import_id}/repair")
async def studio_import_repair(campaign_id: str, import_id: str,
                               principal: ActivityPrincipal = Depends(principal_from_header)) -> dict:
    from app.core.errors import ConflictError, NotFoundError, ValidationError
    from app.services.campaigns.canon_import import CanonImportService

    db = get_database()
    async with db.unit_of_work() as s:
        _, member = await _member_or_403(s, campaign_id, principal)
        _require_owner(member)
        try:
            result = await CanonImportService(s).repair_protocols(
                import_id=import_id, campaign_id=campaign_id)
        except (ConflictError, ValidationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "REPAIRED", **result}
