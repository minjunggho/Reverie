"""Regression: the BELIEF_STANCE → PRIMARY_DEITY transition in character creation.

Root cause (before the fix): the belief "broad" stage both classified stance AND
resolved a deity from free text — so "I believe" reached three different states
(button→deity, typed-deity→details SKIP, typed-unrecognized→broad loop), and the deity
render hard-failed with a diagnostic when the campaign had no active pantheon. A player
could reach class/review without ever explicitly choosing a deity, or hit a dead end.

These tests drive the ACTUAL production path (Discord inbound → game/admin bridge →
CreationFlowService → BuildFlow) and pin: believer ALWAYS reaches PRIMARY_DEITY (never a
skip, loop, or dead-end); PRIMARY_DEITY requires an explicit choice; resume repairs a
believer left without a deity; only atheist/agnostic/former/skip bypass deity selection.
"""
from __future__ import annotations

from sqlalchemy import select

from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.character_draft import CharacterDraft
from app.presentation import MessageKind
from app.services.campaigns.build_flow import (
    BELIEF_AGNOSTIC,
    BELIEF_ATHEIST,
    BELIEF_CHOOSE,
    BELIEF_FINISH,
    BELIEF_MULTI,
    BELIEF_NO_NAMED_DEITY,
    BELIEF_SECONDARY_DONE,
    BELIEF_SECRET,
)
from app.services.faith import FaithService
from tests.support.factories import build_world

_c = {"v": 0}


class Table:
    def __init__(self, db, provider):
        self.game = build_bridge(db, provider=provider)
        self.admin = AdminBridge(db, provider, creation_flow=self.game.creation_flow,
                                 session_zero=self.game.session_zero)

    async def send(self, content, author="disc-p1", name="กี้"):
        _c["v"] += 1
        inbound = InboundMessage(
            discord_message_id=f"bd-{_c['v']}", guild_id="guild-1", channel_id="chan-1",
            author_discord_id=author, author_display_name=name, content=content)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def _activate_fr(db, campaign_id):
    async with db.unit_of_work() as s:
        await FaithService(s).activate_pantheon(campaign_id, "forgotten_realms")


async def _seed_belief(db, member_id, campaign_id, *, stage="broad",
                       char_class="bard", extra=None):
    build = {"step": "belief", "belief_stage": stage, "class": char_class,
             "component_token": "tok"}
    build.update(extra or {})
    async with db.unit_of_work() as s:
        # keep the one-active-draft invariant across parametrized reuse
        from sqlalchemy import update
        await s.execute(update(CharacterDraft).where(
            CharacterDraft.member_id == member_id,
            CharacterDraft.status == "ACTIVE").values(status="CANCELLED"))
        draft = CharacterDraft(campaign_id=campaign_id, member_id=member_id,
                               data={"name": "เทสเตอร์", "_build": build})
        s.add(draft)
        await s.flush()
        return draft.id


async def _draft(db, member_id):
    async with db.session() as s:
        return (await s.execute(select(CharacterDraft).where(
            CharacterDraft.member_id == member_id,
            CharacterDraft.status == "ACTIVE"))).scalars().first()


def _stage(draft):
    return draft.data["_build"].get("belief_stage")


def _profile(draft):
    return draft.data["_build"].get("belief_profile")


# 1 — believer -> deity (the core transition), with version increment + renderer -------
async def test_believer_button_reaches_primary_deity(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    before = (await _draft(db, world.p1_member_id)).version

    table = Table(db, provider)
    r = await table.send(BELIEF_CHOOSE)

    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "deity"                      # explicit PRIMARY_DEITY, not details
    assert d.version == before + 1                   # exactly one persisted mutation
    card = r.responses[0]
    assert card.kind == MessageKind.CHARACTER_CREATION
    assert "เลือกเทพ" in (card.title or "")          # the deity-selection card, not an error
    assert card.choices                              # a real, choosable legal list


# 2 — believer with no pantheon activated yet: the default (Forgotten Realms — Core)
# auto-activates the first time the belief step runs, so PRIMARY_DEITY offers real
# deity choices instead of the empty-pantheon placeholder — never a dead end -----------
async def test_believer_with_no_active_pantheon_auto_activates_the_default(db, provider):
    world = await build_world(db)  # no pantheon activated yet
    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    table = Table(db, provider)

    r = await table.send(BELIEF_CHOOSE)
    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "deity"
    card = r.responses[0]
    assert card.kind == MessageKind.CHARACTER_CREATION       # NOT TECHNICAL_ERROR
    assert card.choices                                      # real deities offered
    assert BELIEF_NO_NAMED_DEITY not in card.choices          # the empty-pantheon fallback did not fire

    async with db.session() as s:
        active = await FaithService(s).list_active_pantheons(world.campaign_id)
    assert [p.key for p in active] == ["forgotten_realms"]    # auto-activated, persisted


# 2b — the explicit "no named deity" resolution still exists as a safety net if the
# default pack were ever unavailable/disabled (defensive path, not the normal case) ----
async def test_believer_no_deity_named_is_still_a_valid_explicit_choice(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    await _seed_belief(db, world.p1_member_id, world.campaign_id, stage="deity")
    table = Table(db, provider)

    r = await table.send(BELIEF_NO_NAMED_DEITY)
    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "details"
    assert _profile(d)["stance"] == "BELIEVER"
    assert _profile(d)["primary_deity_key"] is None          # believer, no canon deity chosen


# 3 — believer names Bahamut (unavailable in this pantheon): flow survives, offers path -
async def test_believer_unavailable_deity_does_not_break_flow(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    table = Table(db, provider)

    await table.send(BELIEF_CHOOSE)
    r = await table.send("Bahamut")                          # not in Forgotten Realms
    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "deity"                              # stays on PRIMARY_DEITY
    assert r.responses[0].kind == MessageKind.CHARACTER_CREATION  # a notice, not a crash
    # Recovering with an available deity continues normally.
    await table.send("Tyr")
    d2 = await _draft(db, world.p1_member_id)
    assert _stage(d2) == "details" and _profile(d2)["primary_deity_key"] == "tyr"


# 4/5 — believer with Tyr, and with Shar: explicit choice persists the right deity ------
async def test_believer_selects_tyr_then_shar(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    table = Table(db, provider)

    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    await table.send(BELIEF_CHOOSE)
    await table.send("Tyr")
    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "details" and _profile(d)["primary_deity_key"] == "tyr"
    assert _profile(d)["visibility"] == "PUBLIC"             # public believer

    await _seed_belief(db, world.p2_member_id, world.campaign_id)
    await table.send(BELIEF_CHOOSE, author="disc-p2", name="โบ")
    await table.send("Shar", author="disc-p2", name="โบ")
    d2 = await _draft(db, world.p2_member_id)
    assert _profile(d2)["primary_deity_key"] == "shar"


# 6 — believer survives a "restart": a fresh flow renders the persisted deity state -----
async def test_believer_state_survives_restart(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    await Table(db, provider).send(BELIEF_CHOOSE)
    await Table(db, provider).send("Tyr")
    # A brand-new Table (fresh flow objects, as after a process restart) resumes it.
    fresh = Table(db, provider)
    r = await fresh.send("!rv resume")
    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "details" and _profile(d)["primary_deity_key"] == "tyr"
    assert r.responses[0].kind == MessageKind.CHARACTER_CREATION


# 7 — resume REPAIRS a believer left without a deity, preserving prior answers ----------
async def test_resume_repairs_believer_without_deity_to_primary_deity(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    # A draft broken mid-transition: believer intent set, but the cursor drifted to
    # details with no deity — the exact shape the old bug could leave behind.
    await _seed_belief(db, world.p1_member_id, world.campaign_id, stage="details",
                       extra={"belief_intent": "believer",
                              "belief_deity_hint": "Tyr"})
    r = await Table(db, provider).send("!rv resume")
    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "deity"                              # repaired to PRIMARY_DEITY
    assert d.data.get("name") == "เทสเตอร์"                   # prior writing intact
    assert "เลือกเทพ" in (r.responses[0].title or "")


# 8 — a stale stance button clicked at the details card does not corrupt state ----------
async def test_stale_stance_button_at_details_is_rejected(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    table = Table(db, provider)
    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    await table.send(BELIEF_CHOOSE)
    await table.send("Tyr")                                  # now at details, deity=tyr
    before = _profile(await _draft(db, world.p1_member_id))

    r = await table.send(BELIEF_CHOOSE)                      # stale broad button
    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "details"                           # unchanged
    assert _profile(d) == before                            # no duplicate/overwrite
    assert "การ์ดก่อนหน้า" in r.responses[0].content        # stale-button notice


# 9 — duplicate believer click: the second is handled without a second transition -------
async def test_duplicate_believer_click_is_idempotent(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    table = Table(db, provider)
    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    await table.send(BELIEF_CHOOSE)
    v1 = (await _draft(db, world.p1_member_id)).version
    # Same broad button again — now stale at the deity stage; must not re-transition
    # or corrupt, and must not silently pick a deity.
    await table.send(BELIEF_CHOOSE)
    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "deity" and _profile(d) is None
    assert d.version >= v1                                   # no lost/duplicated deity


# 10 — concurrent players keep independent belief drafts --------------------------------
async def test_concurrent_players_are_independent(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    table = Table(db, provider)
    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    await _seed_belief(db, world.p2_member_id, world.campaign_id)
    await table.send(BELIEF_CHOOSE, author="disc-p1", name="กี้")
    await table.send("Tyr", author="disc-p1", name="กี้")
    await table.send(BELIEF_CHOOSE, author="disc-p2", name="โบ")
    await table.send("Shar", author="disc-p2", name="โบ")
    assert _profile(await _draft(db, world.p1_member_id))["primary_deity_key"] == "tyr"
    assert _profile(await _draft(db, world.p2_member_id))["primary_deity_key"] == "shar"


# 11 — secret believer still goes through explicit PRIMARY_DEITY -------------------------
async def test_secret_believer_reaches_deity_then_secret_profile(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    table = Table(db, provider)
    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    await table.send(BELIEF_SECRET)
    assert _stage(await _draft(db, world.p1_member_id)) == "deity"
    await table.send("Tyr")
    p = _profile(await _draft(db, world.p1_member_id))
    assert p["stance"] == "SECRET_BELIEVER" and p["visibility"] == "SECRET"
    assert p["primary_deity_key"] == "tyr"


# 12 — multi-faith: primary then a secondary deity, both explicit -----------------------
async def test_multi_faith_primary_then_secondary(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    table = Table(db, provider)
    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    await table.send(BELIEF_MULTI)
    assert _stage(await _draft(db, world.p1_member_id)) == "deity"
    await table.send("Tyr")
    assert _stage(await _draft(db, world.p1_member_id)) == "secondary"
    await table.send("Shar")
    await table.send(BELIEF_SECONDARY_DONE)
    p = _profile(await _draft(db, world.p1_member_id))
    assert p["primary_deity_key"] == "tyr" and "shar" in p["secondary_deity_keys"]
    assert _stage(await _draft(db, world.p1_member_id)) == "details"


# 13 — typed deity at the stance step no longer SKIPS deity selection --------------------
async def test_typed_deity_at_stance_still_requires_explicit_selection(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    table = Table(db, provider)
    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    r = await table.send("ข้านับถือเทพ Tyr อย่างสุดใจ")     # free text naming a deity
    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "deity"                             # NOT details — no skip
    assert _profile(d) is None                              # nothing chosen yet
    assert "เลือกเทพ" in (r.responses[0].title or "")       # PRIMARY_DEITY card shown


# 14/15/16 — atheist / agnostic bypass deity selection (only these may) ------------------
async def test_atheist_and_agnostic_bypass_deity(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    table = Table(db, provider)

    await _seed_belief(db, world.p1_member_id, world.campaign_id)
    await table.send(BELIEF_ATHEIST)
    p = _profile(await _draft(db, world.p1_member_id))
    assert _stage(await _draft(db, world.p1_member_id)) == "details"
    assert p["stance"] == "ATHEIST" and p["primary_deity_key"] is None

    await _seed_belief(db, world.p2_member_id, world.campaign_id)
    await table.send(BELIEF_AGNOSTIC, author="disc-p2", name="โบ")
    p2 = _profile(await _draft(db, world.p2_member_id))
    assert _stage(await _draft(db, world.p2_member_id)) == "details"
    assert p2["stance"] == "AGNOSTIC"


# 17 — details -> finish never re-shows a duplicate belief profile card ------------------
async def test_finish_does_not_duplicate_profile(db, provider):
    world = await build_world(db)
    await _activate_fr(db, world.campaign_id)
    table = Table(db, provider)
    await _seed_belief(db, world.p1_member_id, world.campaign_id, char_class="bard",
                       extra={"species": "human", "background": "sage",
                              "scores": {"str": 8, "dex": 14, "con": 12,
                                         "int": 13, "wis": 10, "cha": 15},
                              "skills": ["arcana", "history"]})
    await table.send(BELIEF_CHOOSE)
    await table.send("Tyr")
    before = _profile(await _draft(db, world.p1_member_id))
    await table.send(BELIEF_FINISH)
    d = await _draft(db, world.p1_member_id)
    # Finishing advances OUT of belief (to review); the single profile is unchanged.
    assert d.data["_build"]["step"] == "review"
    assert _profile(d) == before


# 18 — exact live-playtest regression: a Cleric on a brand-new campaign (no pantheon ever
# activated) must reach the cleric power-source card, never the "class=cleric;
# pool=cleric_deity; legal_count=0" dead-end diagnostic ---------------------------------
async def test_cleric_on_fresh_campaign_reaches_power_source_selection(db, provider):
    world = await build_world(db)  # no pantheon activated — the exact reported scenario
    await _seed_belief(db, world.p1_member_id, world.campaign_id, char_class="cleric",
                       stage="cleric_deity")
    table = Table(db, provider)

    r = await table.send("hello")   # any input re-renders the current belief step
    card = r.responses[0]
    assert card.kind == MessageKind.CHARACTER_CREATION
    assert "สร้างตัวละครต่อไม่ได้" not in card.content       # NOT the dead-end diagnostic
    assert "legal_count=0" not in card.content
    assert card.choices                                       # real cleric-capable deities
    assert any("Tyr" in c or "ทีร์" in c for c in card.choices)

    r2 = await table.send("Tyr")
    d = await _draft(db, world.p1_member_id)
    assert _stage(d) == "cleric_domain"                       # advances past the old dead-end
    assert r2.responses[0].kind == MessageKind.CHARACTER_CREATION
