"""Thai campaign import — canonical keys and the starting location (Failures 4 & 5).

The old ASCII-only `_slug` stripped every Thai character, so ALL Thai-named locations
collapsed to the key "x": a two-location Thai campaign failed 'location keys must be
unique', and a Thai `Opening Location:` line could never match its location — forcing
owners to type `!rv session start at <location>` manually. These tests pin the fix:
Thai names produce distinct canonical keys, Thai connections link, the imported
opening location becomes the campaign's starting location, and session-start location
resolution succeeds with no manual location.
"""
from __future__ import annotations

from sqlalchemy import select

from app.discord_bridge import AdminBridge, InboundMessage
from app.discord_bridge.dto import InboundAttachment
from app.models.campaign import Campaign
from app.models.location import Location
from app.models.world_graph import LocationConnection
from app.services.sessions import SessionOpeningService

_n = {"v": 0}


def _msg(content, *, author="owner", attachment=None):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"th{_n['v']}", guild_id="g", channel_id="chan-1",
        author_discord_id=author, author_display_name=author, content=content,
        attachments=(attachment,) if attachment else ())


def _thai_campaign_md(opening_location_line: str) -> bytes:
    return (
        "# Campaign: มงกุฎเลือด\n\n"
        "## Brief\n"
        "อาณาจักรใต้เงามงกุฎเลือด ผู้คนกระซิบถึงคืนจันทร์ดับ\n\n"
        "## Central Question\n"
        "ใครจะหยุดมงกุฎเลือดได้ก่อนคืนจันทร์ดับ?\n\n"
        "## Location: โบสถ์แสงสุดท้าย\n"
        "### Obvious\n"
        "โบสถ์หินเก่าแก่ แสงเทียนไม่เคยดับ\n"
        "### Connections\n"
        "ห้องใต้ดินเก็บไวน์\n\n"
        "## Location: ห้องใต้ดินเก็บไวน์\n"
        "### Obvious\n"
        "ชั้นไวน์เรียงราย อากาศเย็นชื้น\n\n"
        "## Session 1\n"
        "### Opening Location\n"
        f"{opening_location_line}\n"
        "### Current Activity\n"
        "พิธีสวดเย็นกำลังเริ่ม\n"
    ).encode("utf-8")


async def _import_and_approve(db, provider, md: bytes):
    from app.models.canon_import import CanonImport

    admin = AdminBridge(db, provider)
    await admin.handle(_msg("!rv campaign new มงกุฎเลือด"))
    await admin.handle(_msg(
        "!rv campaign import",
        attachment=InboundAttachment("crimson.md", "text/markdown", md)))
    async with db.session() as s:
        draft = (await s.execute(select(CanonImport))).scalar_one()
    await admin.handle(_msg(f"!rv campaign import approve {draft.id}"))
    async with db.session() as s:
        campaign = (await s.execute(select(Campaign))).scalar_one()
        locations = list((await s.execute(select(Location))).scalars())
        edges = list((await s.execute(select(LocationConnection))).scalars())
    return campaign, locations, edges


async def test_thai_locations_get_distinct_keys_and_start_location(db, provider):
    campaign, locations, edges = await _import_and_approve(
        db, provider, _thai_campaign_md("โบสถ์แสงสุดท้าย"))

    # Two DISTINCT locations — Thai names no longer collapse to one key.
    names = sorted(loc.name for loc in locations)
    assert names == ["ห้องใต้ดินเก็บไวน์", "โบสถ์แสงสุดท้าย"]
    church = next(loc for loc in locations if loc.name == "โบสถ์แสงสุดท้าย")
    cellar = next(loc for loc in locations if loc.name == "ห้องใต้ดินเก็บไวน์")
    # The Thai-keyed connection linked the right rooms.
    assert any(e.from_location_id == church.id and e.to_location_id == cellar.id
               for e in edges)
    # The imported opening location IS the campaign's starting location.
    assert campaign.starting_location_id == church.id
    # And session start needs no manual location.
    resolved = await SessionOpeningService(db, provider).resolve_opening_location(
        campaign_id=campaign.id, attendance_member_ids=[])
    assert resolved == church.id


async def test_prose_opening_location_resolves_by_name_fallback(db, provider):
    """Prose that doesn't slug-match a key ('ที่โบสถ์แสงสุดท้าย') still resolves —
    by unique name containment — instead of silently dropping the start."""
    campaign, locations, _ = await _import_and_approve(
        db, provider, _thai_campaign_md("ที่โบสถ์แสงสุดท้าย"))
    church = next(loc for loc in locations if loc.name == "โบสถ์แสงสุดท้าย")
    assert campaign.starting_location_id == church.id


# --- aliases: one canonical place answering to many names (Failure 4) --------------

_ALIASED_MD = (
    "# Campaign: มงกุฎเลือด\n\n"
    "## Central Question\nใครจะหยุดมงกุฎเลือด?\n\n"
    "## Location: โบสถ์แสงสุดท้าย\n"
    "### Obvious\nโบสถ์หินเก่าแก่\n"
    "### Connections\nห้องใต้ดินเก็บไวน์\n\n"
    "## Location: ห้องใต้ดินเก็บไวน์\n"
    "### Obvious\nชั้นไวน์เรียงราย\n"
    "### Aliases\n"
    "- ห้องใต้ดิน\n"
    "- ห้องเก็บไวน์\n"
    "- wine cellar\n"
    "- church basement\n\n"
    "## Session 1\n"
    "### Opening Location\nโบสถ์แสงสุดท้าย\n"
).encode("utf-8")


async def test_imported_aliases_resolve_to_one_canonical_location(db, provider):
    """'ห้องใต้ดิน', 'wine cellar', and 'ห้องเก็บไวน์' are ONE place: aliases are
    imported onto the canonical row and every alias resolves to the same id."""
    from app.world.location_resolver import LocationResolver

    campaign, locations, _ = await _import_and_approve(db, provider, _ALIASED_MD)
    cellar = next(loc for loc in locations if loc.name == "ห้องใต้ดินเก็บไวน์")
    assert "wine cellar" in cellar.aliases and "ห้องใต้ดิน" in cellar.aliases

    async with db.session() as s:
        resolver = LocationResolver(s)
        for ref in ("ห้องใต้ดิน", "ห้องเก็บไวน์", "wine cellar", "church basement"):
            result = await resolver.resolve(campaign_id=campaign.id, reference=ref)
            assert result.resolved, f"alias {ref!r} did not resolve"
            assert result.match.location.id == cellar.id, f"alias {ref!r} split the room"
    # No duplicate room was created for any alias.
    assert len(locations) == 2


async def test_alias_collision_is_a_loud_preview_warning(db, provider):
    """The same alias answering for two locations makes references ambiguous —
    the import preview must say so, not let players get split later."""
    from sqlalchemy import select

    from app.discord_bridge.dto import InboundAttachment
    from app.models.canon_import import CanonImport

    md = (
        "# Campaign: ซ้ำซ้อน\n\n"
        "## Location: หอเหนือ\n### Obvious\nหอคอยด้านเหนือ\n"
        "### Aliases\n- หอคอย\n\n"
        "## Location: หอใต้\n### Obvious\nหอคอยด้านใต้\n"
        "### Aliases\n- หอคอย\n"
    ).encode("utf-8")
    admin = AdminBridge(db, provider)
    await admin.handle(_msg("!rv campaign new ซ้ำซ้อน"))
    await admin.handle(_msg(
        "!rv campaign import",
        attachment=InboundAttachment("dup.md", "text/markdown", md)))
    async with db.session() as s:
        draft = (await s.execute(select(CanonImport))).scalar_one()
    warnings = " ".join(draft.proposal["_review"]["warnings"])
    assert "หอคอย" in warnings and "multiple locations" in warnings
