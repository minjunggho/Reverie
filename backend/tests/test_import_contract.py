"""The import contract: what the engine expects, and what it tells the author it did.

Two symptoms in one place. A prose campaign — many places, few or no authored exits —
became a scatter of unreachable islands: validate_world_graph only flagged a location
with no exits when it had a parent OR inbound edges, so a location with no exits, no
inbound edges, and no parent fell through both branches and was accepted in silence
(docs/progression-audit.md, RC7). And the author got no report of what was imported,
inferred, ignored, or missing.

These tests pin: connectivity is repaired where it can be, the islands that cannot be
repaired are surfaced, and the schema_version 2.0 YAML block maps to the same canonical
proposal as the markdown importer.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.location import Location
from app.models.world_graph import LocationConnection
from app.services.campaigns import CampaignService
from app.services.campaigns.canon_import import (
    CanonImportService,
    parse_campaign_file,
)


async def _import(db, channel: str, text: str, filename="c.md"):
    async with db.unit_of_work() as s:
        camp = CampaignService(s)
        campaign = await camp.create_campaign(
            name="imp", discord_guild_id="g", game_channel_id=channel,
            owner_discord_user_id=f"owner-{channel}", owner_display_name="DM")
        await camp.activate_campaign(campaign.id)
        owner = await camp.resolve_member(campaign.id, f"owner-{channel}")
        draft = await CanonImportService(s).create_draft(
            campaign_id=campaign.id, uploader_member_id=owner.id,
            filename=filename, data=text.encode("utf-8"))
        cid, did = campaign.id, draft.id
    async with db.unit_of_work() as s:
        review = await CanonImportService(s).approve(import_id=did, campaign_id=cid)
    return cid, review


# --- connectivity: prose locations get connected, true islands get surfaced -----

_PARENTED_ISLANDS = """# Campaign: เมืองที่กระจัดกระจาย

## Central Question
ใครเผาหอคอย

## Chapter: บทแรก
### key
ch1
### goal
สืบหาคนวางเพลิง

## Location: เมือง
### key
town
### type
SETTLEMENT

## Location: จัตุรัส
### key
plaza
### parent
town

## Location: โรงเตี๊ยม
### key
inn
### parent
town

## Location: หอคอย
### key
tower
### parent
town
"""


async def test_parented_locations_with_no_exits_are_connected_not_stranded(db, provider):
    """The prose case: places written with a settlement parent but no authored exits.
    The engine infers the exterior links so the whole town is reachable, and reports
    what it added."""
    cid, review = await _import(db, "chan-parented", _PARENTED_ISLANDS)
    assert review.inferred_connectors, "the engine must connect parented islands"
    assert not review.orphans, "nothing parented should remain stranded"

    async with db.session() as s:
        edges = (await s.execute(select(LocationConnection).where(
            LocationConnection.campaign_id == cid))).scalars().all()
    # Every child now has a way to and from its parent.
    assert edges, "exterior links must be committed, not just reported"


_TRUE_ORPHAN = """# Campaign: เกาะที่ไปไม่ถึง

## Central Question
มีอะไรอยู่บนเกาะร้าง

## Chapter: บทแรก
### key
ch1
### goal
ไปให้ถึงเกาะ

## Location: ท่าเรือ
### key
dock
### obvious
ท่าเรือไม้เก่า
### exits
- ไปตลาด / เดิน / 5 นาที -> market

## Location: ตลาด
### key
market
### exits
- กลับท่าเรือ / เดิน / 5 นาที -> dock

## Location: เกาะร้าง
### key
island
### obvious
เกาะที่ไม่มีทางไป
"""


async def test_a_parentless_unreachable_island_is_surfaced_not_hidden(db, provider):
    """The exact gap: a location with no exits, no inbound edges, and no parent. It
    cannot be connected without inventing canon, so the engine reports it for the owner
    instead of accepting it in silence."""
    cid, review = await _import(db, "chan-orphan", _TRUE_ORPHAN)
    assert "เกาะร้าง" in review.orphans
    assert any("no route from the start" in w.lower() for w in review.warnings)
    report = review.as_report()
    assert "เกาะร้าง" in report and "UNREACHABLE" in report


async def test_a_well_connected_campaign_reports_no_orphans(db, provider):
    cid, review = await _import(db, "chan-clean", _PARENTED_ISLANDS)
    assert review.orphans == []


# --- the report tells the author what happened ---------------------------------

async def test_the_report_counts_the_new_content_types(db, provider):
    cid, review = await _import(db, "chan-counts", _PARENTED_ISLANDS)
    assert review.counts["chapters"] == 1
    assert review.counts["locations"] == 4


async def test_a_campaign_with_no_chapters_is_warned_it_has_no_direction(db, provider):
    no_chapters = """# Campaign: แผนที่เฉยๆ

## Central Question
ทำไมถึงเงียบ

## Location: ห้อง
### key
room
### obvious
ห้องว่าง
"""
    cid, review = await _import(db, "chan-nochapters", no_chapters)
    assert any("no objective layer" in w.lower() or "no chapters" in w.lower()
               for w in review.warnings)


# --- the schema_version 2.0 YAML block maps to the same proposal ----------------

_V2_YAML = """# Campaign notes for the DM

Some prose the author keeps for themselves.

```yaml
schema_version: "2.0"
campaign:
  name: เมืองท่าที่เรือไม่ออก
  central_question: ใครกักเรือทั้งเมือง
starting_state:
  location: harbor
chapters:
  - key: ch1
    name: ท่าเรือเงียบ
    goal: หาว่าทำไมเรือออกไม่ได้
objectives:
  - key: obj-ask
    name: ถามนายท่า
    task: ถามนายท่าเฒ่า
    chapter: ch1
locations:
  - key: harbor
    name: ท่าเรือเก่า
    obvious: ท่าเรือหินเก่า
    exits:
      - to: road
        label: ถนนริมน้ำ
  - key: road
    name: ถนนริมน้ำ
    exits:
      - to: harbor
lore:
  - เมืองนี้อยู่ได้ด้วยการค้าทางเรือ
routes:
  - from: harbor
    to: road
items:
  - a thing the importer does not consume yet
```
"""


async def test_a_fenced_yaml_v2_block_imports_as_the_canonical_proposal(db, provider):
    _, proposal, review = parse_campaign_file("campaign.md", _V2_YAML.encode("utf-8"))
    assert proposal.identity_name == "เมืองท่าที่เรือไม่ออก"
    assert proposal.central_question == "ใครกักเรือทั้งเมือง"
    assert {c.key for c in proposal.chapters} == {"ch1"}
    assert {o.key for o in proposal.objectives} == {"obj-ask"}
    assert {l.key for l in proposal.locations} == {"harbor", "road"}
    assert proposal.starting_location == "harbor"
    assert any("เรือ" in wf.fact for wf in proposal.world_facts)   # lore -> world_facts


async def test_unconsumed_v2_sections_are_reported_not_silently_dropped(db, provider):
    """routes/items/encounters/events/world_clocks/progression_rules are in the contract
    but not yet consumed — the author must be told, never left guessing."""
    _, _, review = parse_campaign_file("campaign.md", _V2_YAML.encode("utf-8"))
    assert "routes" in review.ignored and "items" in review.ignored
    assert "ignored" in review.as_report()


async def test_a_yaml_block_without_schema_version_is_not_treated_as_a_spec(db, provider):
    """An unrelated yaml fence in an author's notes must not hijack the import."""
    markdown_with_stray_yaml = """# Campaign: ปกติ

## Central Question
อะไรสักอย่าง

```yaml
# just the author's todo list, not a spec
- buy milk
```

## Location: ห้อง
### key
room
### obvious
ห้องหนึ่ง
"""
    _, proposal, _ = parse_campaign_file(
        "c.md", markdown_with_stray_yaml.encode("utf-8"))
    assert {l.key for l in proposal.locations} == {"room"}   # markdown path was used


async def test_a_standalone_yaml_file_imports(db, provider):
    yaml_only = """schema_version: "2.0"
campaign:
  name: แคมเปญ YAML
  central_question: อะไร
locations:
  - key: start
    name: จุดเริ่ม
    obvious: ที่แห่งหนึ่ง
"""
    _, proposal, _ = parse_campaign_file("campaign.yaml", yaml_only.encode("utf-8"))
    assert proposal.identity_name == "แคมเปญ YAML"
    assert {l.key for l in proposal.locations} == {"start"}
