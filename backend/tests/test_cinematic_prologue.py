"""Cinematic Session-1 prologue: a grand, large-to-small opening that names the
campaign's main goal and never leaks DM-only canon.

Gate: the prologue is generated only when the campaign has a known main goal
(central question / main-story goal). Without one, the standard hook-aware opening
is used unchanged — the engine never has the AI invent a world bible from nothing.
"""
from __future__ import annotations

from app.models.campaign import Campaign
from app.models.character import Character
from app.models.enums import Visibility
from app.models.knowledge import Secret
from app.models.location import Location
from app.models.world import Threat
from app.models.world_graph import CampaignCanonRecord
from app.presentation import MessageKind
from app.services.sessions import SessionOpeningService
from tests.support.factories import build_world

_MAIN_GOAL = "ตามหาและทำลายหัวใจของราชากลวงก่อนการฟื้นคืนชีพจะกลืนกินอาณาจักรเหนือ"


async def _seed_world_canon(db, world) -> None:
    async with db.unit_of_work() as s:
        campaign = await s.get(Campaign, world.campaign_id)
        campaign.central_question = _MAIN_GOAL
        campaign.brief = "ยุคที่เทพเงียบงันและอาณาจักรเก่ากำลังล่มสลาย"
        # A character hook the prologue can draw on.
        kael = await s.get(Character, world.kael_id)
        kael.hooks = {"desire": "ตามหาน้องสาวที่หายไปในสงคราม"}
        # Player-safe world lore (PUBLIC) — should be available to the prologue.
        s.add(CampaignCanonRecord(
            campaign_id=world.campaign_id, category="religion",
            fact="ศรัทธาเก่าเสื่อมถอย วิหารร้างผู้คน", visibility=Visibility.PUBLIC.value,
            importance=40))
        # A named looming power (identity is public; its hidden plan is not fed in).
        s.add(Threat(campaign_id=world.campaign_id, name="ราชากลวง",
                     goal="ฟื้นคืนชีพและกลืนกินอาณาจักรเหนือ",
                     next_action="SECRET_พลีชีพนักบวชคนสุดท้ายเที่ยงคืนนี้", status="active"))
        # A DM-only secret that must never surface in the player-facing prologue.
        s.add(Secret(campaign_id=world.campaign_id,
                     fact="SECRET_หัวใจซ่อนอยู่ใต้บัลลังก์ของราชาเมืองเอง",
                     visibility=Visibility.DM_ONLY.value))


async def test_session_one_emits_cinematic_prologue_with_main_goal(db, provider):
    world = await build_world(db)
    await _seed_world_canon(db, world)

    result = await SessionOpeningService(db, provider).open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
    )
    assert result.number == 1

    # The world-scale movements are their own CAMPAIGN_PROLOGUE frames, large to
    # small and each breathing on its own, distinct from the opening SCENE_FRAME:
    # world & powers → the conflict that changed everything → the descent to the party.
    prologue_frames = [m for m in result.messages if m.kind == MessageKind.CAMPAIGN_PROLOGUE]
    assert len(prologue_frames) == 3
    assert any(m.title == "เส้นทางสู่พวกเจ้า" for m in prologue_frames)
    assert any(m.kind == MessageKind.SCENE_FRAME for m in result.messages)

    # Canon fidelity: the descent ends at the REAL opening place, not an invented one.
    async with db.session() as s:
        opening_name = (await s.get(Location, world.location_id)).name
    assert any(opening_name in m.content for m in prologue_frames)

    # The main goal is surfaced unmistakably as a field on the opening scene frame.
    goal_values = [
        f.get("value", "")
        for m in result.messages
        for f in (m.data.get("fields") or [])
    ]
    assert any(_MAIN_GOAL in v for v in goal_values)


async def test_prologue_never_leaks_dm_only_canon(db, provider):
    world = await build_world(db)
    await _seed_world_canon(db, world)

    result = await SessionOpeningService(db, provider).open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
    )
    blob = "\n".join(
        [m.content for m in result.messages]
        + [f.get("value", "") for m in result.messages for f in (m.data.get("fields") or [])]
    )
    assert "SECRET_" not in blob


async def test_gather_world_canon_builds_geography_ladder_and_present_npcs(db, provider):
    """The enriched gather feeds the model real structure: a largest→smallest
    geography ladder ending at the opening place, the NPCs standing there, and never
    any DM-tagged canon (even PUBLIC + high-importance rows tagged SECRET are dropped)."""
    world = await build_world(db)
    await _seed_world_canon(db, world)
    async with db.unit_of_work() as s:
        region = Location(campaign_id=world.campaign_id, name="แคว้นเหนือ",
                          location_type="REGION")
        s.add(region)
        await s.flush()
        town = Location(campaign_id=world.campaign_id, name="เกรย์แมนเทิล",
                        location_type="SETTLEMENT", parent_id=region.id)
        s.add(town)
        await s.flush()
        opening = await s.get(Location, world.location_id)
        opening.parent_id = town.id
        opening_name = opening.name
        # A PUBLIC-but-DM-tagged fact must be dropped by the defensive SECRET filter.
        s.add(CampaignCanonRecord(
            campaign_id=world.campaign_id, category="history",
            fact="SECRET_ประวัติที่ไม่ควรหลุด", visibility=Visibility.PUBLIC.value,
            importance=99))

    world_ctx = await SessionOpeningService(db, provider)._gather_world_canon(
        world.campaign_id, world.location_id)

    kinds = [k for k, _ in world_ctx["geography"]]
    names = [n for _, n in world_ctx["geography"]]
    assert kinds[0] == "REGION" and names[0] == "แคว้นเหนือ"   # camera starts largest
    assert "เกรย์แมนเทิล" in names                             # the settlement between
    assert names[-1] == opening_name                          # and ends at the exact place
    # The guard standing at the opening location is surfaced (name only).
    assert any(name == "ยามเฝ้าประตู" for name, _ in world_ctx["npcs_present"])
    # Nothing DM-tagged reaches the prologue, even a high-importance PUBLIC row.
    blob = " ".join(world_ctx["lore"] + world_ctx["powers"]
                    + [n for n, _ in world_ctx["npcs_present"]])
    assert "SECRET_" not in blob


async def test_no_main_goal_falls_back_to_standard_opening(db, provider):
    """No central question → no invented world; the standard opening runs instead,
    so there is no 🎬 prologue frame and no main-goal field."""
    world = await build_world(db)  # build_world sets no central_question

    result = await SessionOpeningService(db, provider).open_new_session(
        campaign_id=world.campaign_id,
        attendance_member_ids=[world.p1_member_id, world.p2_member_id],
        location_id=world.location_id,
    )
    assert not any(m.kind == MessageKind.CAMPAIGN_PROLOGUE for m in result.messages)
    field_names = [
        f.get("name", "")
        for m in result.messages
        for f in (m.data.get("fields") or [])
    ]
    assert not any("เป้าหมายหลัก" in n for n in field_names)
