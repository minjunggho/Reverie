"""A clue OPENS something. It is not a line of prose.

Clues were `list[str]` on Secret, on Scene.allowed_clues, and in main_story["leads"] —
free text in three places, linked to nothing. The engine could not know which clue
unlocks which destination because no field existed that could hold the edge, and a
revealed clue was narrated and forgotten (docs/progression-audit.md, RC3).

These tests pin the edge: discovering the torn ledger page makes a hidden dock
routable, opens the route to it, and turns a secret objective into known work.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.campaign_progression import Clue
from app.models.consequences import Quest
from app.models.location import Location
from app.models.world_graph import LocationConnection
from app.services.campaigns import CampaignService
from app.services.campaigns.canon_import import CanonImportService
from app.services.campaigns.clue_service import ClueService

_CAMPAIGN = """# Campaign: เมืองท่าที่เรือไม่ออก

## Central Question
ใครหรืออะไรกักเรือทั้งเมืองไว้

## Chapter: ท่าเรือที่เงียบผิดปกติ
### key
ch-harbor
### goal
หาว่าทำไมเรือถึงออกไม่ได้

## Objective: ลงไปที่ท่าจม
### key
obj-dive
### task
ลงไปดูท่าเรือที่จมอยู่ใต้น้ำ
### chapter
ch-harbor

## Location: ท่าเรือเก่า
### key
old-harbor
### obvious
ท่าเรือหินที่เรือจอดแน่นขนัด
### exits
- ทางลงใต้น้ำ / ลงไป / 10 นาที -> sunken-dock

## Location: ท่าจมใต้น้ำ
### key
sunken-dock
### discovery
HIDDEN
### obvious
ท่าเรือเก่าที่จมอยู่ใต้ผิวน้ำ

## Clue: หน้าที่ถูกฉีก
### key
clue-torn-page
### text
หน้าที่ถูกฉีกในสมุดท่าเรือพูดถึงท่าเรือเก่าที่จมอยู่ใต้น้ำ
### location
old-harbor
### reveals
- location: sunken-dock
- route: old-harbor->sunken-dock
- objective: obj-dive
- fact: มีท่าเรืออีกแห่งจมอยู่ใต้น้ำ

## Secret: สัญญาใต้น้ำ
### key
the-pact
### truth
นายท่าขายทางออกของเมืองแลกกับชีวิตลูกสาว
### clues
- ชาวเรือเล่าว่าเห็นแสงใต้น้ำทุกคืนข้างแรม
"""


async def _import(db, channel: str, text: str = _CAMPAIGN) -> str:
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        campaign = await camp.create_campaign(
            name="clues", discord_guild_id="g", game_channel_id=channel,
            owner_discord_user_id=f"owner-{channel}", owner_display_name="DM")
        await camp.activate_campaign(campaign.id)
        owner = await camp.resolve_member(campaign.id, f"owner-{channel}")
        draft = await CanonImportService(s).create_draft(
            campaign_id=campaign.id, uploader_member_id=owner.id,
            filename="c.md", data=text.encode("utf-8"))
        cid, did = campaign.id, draft.id
    async with db.unit_of_work() as s:
        await CanonImportService(s).approve(import_id=did, campaign_id=cid)
    return cid


async def _clue(s, cid: str, key: str) -> Clue:
    return (await s.execute(select(Clue).where(
        Clue.campaign_id == cid, Clue.key == key))).scalars().one()


async def _location(s, cid: str, name_fragment: str) -> Location:
    rows = (await s.execute(select(Location).where(
        Location.campaign_id == cid))).scalars().all()
    return next(l for l in rows if name_fragment in l.name)


# --- import produces edges, not prose ------------------------------------------

async def test_import_creates_clues_with_typed_reveal_edges(db, provider):
    cid = await _import(db, "chan-clue-import")
    async with db.session() as s:
        clue = await _clue(s, cid, "clue-torn-page")
        dock = await _location(s, cid, "ท่าจม")
    kinds = {r["kind"] for r in clue.reveals}
    assert kinds == {"location", "route", "objective", "fact"}
    # Location refs are resolved to real ids at import — an authored key means nothing
    # at runtime.
    loc_ref = next(r["ref"] for r in clue.reveals if r["kind"] == "location")
    assert loc_ref == dock.id


async def test_a_hidden_destination_starts_unroutable(db, provider):
    """If every imported place is KNOWN there is nothing for a clue to unlock and
    discovery stops meaning anything."""
    cid = await _import(db, "chan-hidden")
    async with db.session() as s:
        dock = await _location(s, cid, "ท่าจม")
        harbor = await _location(s, cid, "ท่าเรือเก่า")
    assert dock.discovery_state == "HIDDEN"
    assert harbor.discovery_state == "KNOWN"


async def test_a_clue_gated_objective_is_not_handed_out_when_its_chapter_opens(db, provider):
    """"Dive to the sunken dock" cannot be known work before the party learns the dock
    exists. An opening chapter discovers its objectives — but never the ones a clue
    gates, or the clue would have nothing left to reveal.
    """
    cid = await _import(db, "chan-gated")
    async with db.session() as s:
        quest = (await s.execute(select(Quest).where(
            Quest.campaign_id == cid, Quest.key == "obj-dive"))).scalars().one()
    assert quest.state == "UNKNOWN"


# --- discovering a clue changes the world --------------------------------------

async def test_discovering_a_clue_opens_the_destination_route_and_objective(db, provider):
    """The whole point of the slice: prose becomes an executable edge."""
    cid = await _import(db, "chan-open")
    async with db.unit_of_work() as s:
        clue = await _clue(s, cid, "clue-torn-page")
        effect = await ClueService(s).discover(campaign_id=cid, clue=clue)

    assert effect.opened_anything
    assert not effect.unresolved, f"every authored ref must resolve: {effect.unresolved}"
    assert effect.revealed_objectives == ["obj-dive"]

    async with db.session() as s:
        dock = await _location(s, cid, "ท่าจม")
        harbor = await _location(s, cid, "ท่าเรือเก่า")
        quest = (await s.execute(select(Quest).where(
            Quest.campaign_id == cid, Quest.key == "obj-dive"))).scalars().one()
        edges = (await s.execute(select(LocationConnection).where(
            LocationConnection.campaign_id == cid,
            LocationConnection.from_location_id == harbor.id,
            LocationConnection.to_location_id == dock.id))).scalars().all()
    assert dock.discovery_state == "KNOWN", "the place is now a real destination"
    assert quest.state == "DISCOVERED", "the objective is now known work"
    assert edges and all(e.discovery_state == "KNOWN" for e in edges)


async def test_discovering_a_clue_is_idempotent(db, provider):
    """Two players reading the same page must not double-fire the world."""
    cid = await _import(db, "chan-idem")
    async with db.unit_of_work() as s:
        clue = await _clue(s, cid, "clue-torn-page")
        first = await ClueService(s).discover(campaign_id=cid, clue=clue)
    async with db.unit_of_work() as s:
        clue = await _clue(s, cid, "clue-torn-page")
        second = await ClueService(s).discover(campaign_id=cid, clue=clue)
    assert first.opened_anything
    assert second.already_known and not second.opened_anything


async def test_a_clue_never_resets_an_objective_already_underway(db, provider):
    """A clue found late must not drag a finished objective back to 'newly discovered'."""
    cid = await _import(db, "chan-noreset")
    async with db.unit_of_work() as s:
        quest = (await s.execute(select(Quest).where(
            Quest.campaign_id == cid, Quest.key == "obj-dive"))).scalars().one()
        quest.state = "COMPLETED"
    async with db.unit_of_work() as s:
        clue = await _clue(s, cid, "clue-torn-page")
        effect = await ClueService(s).discover(campaign_id=cid, clue=clue)
    assert "obj-dive" not in effect.revealed_objectives
    async with db.session() as s:
        quest = (await s.execute(select(Quest).where(
            Quest.campaign_id == cid, Quest.key == "obj-dive"))).scalars().one()
    assert quest.state == "COMPLETED"


async def test_a_clue_fact_becomes_party_visible_canon(db, provider):
    cid = await _import(db, "chan-fact")
    async with db.unit_of_work() as s:
        clue = await _clue(s, cid, "clue-torn-page")
        await ClueService(s).discover(campaign_id=cid, clue=clue)
    async with db.session() as s:
        from app.models.world_graph import CampaignCanonRecord
        rows = (await s.execute(select(CampaignCanonRecord).where(
            CampaignCanonRecord.campaign_id == cid,
            CampaignCanonRecord.provenance == "CLUE_DISCOVERED"))).scalars().all()
    assert [r.fact for r in rows] == ["มีท่าเรืออีกแห่งจมอยู่ใต้น้ำ"]
    assert all(r.visibility == "party" or r.visibility == "PARTY" for r in rows)


# --- matching a narrated fragment back to its authored clue ---------------------

async def test_a_narrated_fragment_matches_its_authored_clue(db, provider):
    """The reveal path speaks in fragments, not keys — a partial overheard line must
    still resolve to the clue it came from."""
    cid = await _import(db, "chan-match")
    async with db.session() as s:
        hit = await ClueService(s).match_text(cid, "ท่าเรือเก่าที่จมอยู่ใต้น้ำ")
    assert hit is not None and hit.key == "clue-torn-page"


async def test_secret_bullets_become_trackable_clues_that_open_nothing(db, provider):
    """A bullet under a Secret says what the evidence IS, never what it unlocks — but
    it must still be recognisable so the party's finding it is recorded."""
    cid = await _import(db, "chan-secret-clue")
    async with db.session() as s:
        clue = await _clue(s, cid, "the-pact-clue-1")
    assert "แสงใต้น้ำ" in clue.text
    assert clue.reveals == []
    assert clue.secret_id is not None
