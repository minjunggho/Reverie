"""Player-facing recovery and bounded spell-selection acceptance tests.

These tests deliberately route admin commands through ``AdminBridge`` and typed or
component values through ``DiscordBridge``.  Component values are always taken from
the rendered response, matching the Discord adapter's callback path.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from itertools import count

import discord
import pytest
from sqlalchemy import select

from app.db.session import Database
from app.discord_bridge import AdminBridge, DiscordBridge, InboundMessage
from app.models.character_draft import CharacterDraft
from app.presentation import MessageKind
from app.rules_content import get_registry
from app.rules_content.registry import RulesRegistry, SpellDef
from app.services.campaigns import CampaignService
from app.services.campaigns.build_flow import (
    SPELL_BACK,
    SPELL_CANCEL,
    SPELL_CONFIRM,
    SPELL_NEXT,
    SPELL_PAGE_SIZE,
    SPELL_PREVIOUS,
)
from app.services.campaigns.creation_flow import CreationFlowService
from discord_bot.render import ChoiceView
from tests.support.factories import build_world


_message_ids = count(1)


def _inbound(
    content: str,
    *,
    author: str = "disc-p1",
    name: str = "Player One",
    channel: str = "chan-1",
    guild: str = "guild-1",
) -> InboundMessage:
    return InboundMessage(
        discord_message_id=f"creation-recovery-{next(_message_ids)}",
        guild_id=guild,
        channel_id=channel,
        author_discord_id=author,
        author_display_name=name,
        content=content,
    )


def _bridges(db, provider, *, registry: RulesRegistry | None = None):
    flow = CreationFlowService(db, provider)
    if registry is not None:
        # A per-flow copy keeps large-pool tests from mutating the cached registry.
        flow.build.reg = registry
    return (
        AdminBridge(db, provider, creation_flow=flow),
        DiscordBridge(db, creation_flow=flow),
        flow,
    )


def _complete_build_data(
    step: str,
    *,
    class_name: str = "wizard",
    selected: list[str] | None = None,
) -> dict:
    reg = get_registry()
    background = "sage" if class_name == "wizard" else "acolyte"
    cls = reg.get_class(class_name)
    bg = reg.get_background(background)
    skill_pool = (
        list(reg.skills)
        if cls.skill_choices["options"] == "any"
        else list(cls.skill_choices["options"])
    )
    legal_skills = [key for key in skill_pool if key not in bg.skill_proficiencies]
    skill_count = int(cls.skill_choices["count"])
    build = {
        "step": step,
        "class": class_name,
        "species": "dwarf",
        "background": background,
        "scores": {"str": 8, "dex": 12, "con": 13, "int": 15, "wis": 14, "cha": 10},
        "asi": {},
        "skills": legal_skills[:skill_count],
        "spell_pages": {step: 0},
    }
    if step == "cantrips":
        build["cantrips"] = list(selected or [])
    elif step == "book":
        build["cantrips"] = [
            spell.name for spell in reg.spells_for_class(class_name, 0)[:3]
        ]
        build["book"] = list(selected or [])
    elif step == "prepared":
        cantrip_count = cls.spellcasting.cantrips_known if cls.spellcasting else 0
        build["cantrips"] = [
            spell.name
            for spell in reg.spells_for_class(class_name, 0)[:cantrip_count]
        ]
        if cls.spellcasting and cls.spellcasting.spellbook_size:
            build["book"] = [
                spell.name
                for spell in reg.spells_for_class(class_name, 1)[
                    : cls.spellcasting.spellbook_size
                ]
            ]
        build["prepared"] = list(selected or [])
    return {
        "name": "Persisted Hero",
        "concept": "A patient keeper of forbidden maps",
        "origin": "The northern archive",
        "desire": "Recover a lost atlas",
        "_build": build,
    }


async def _add_draft(
    db: Database, *, campaign_id: str, member_id: str, data: dict, step: int = 3
) -> str:
    async with db.unit_of_work() as session:
        draft = CharacterDraft(
            campaign_id=campaign_id,
            member_id=member_id,
            data=deepcopy(data),
            step=step,
        )
        session.add(draft)
        await session.flush()
        return draft.id


async def _draft_snapshot(db: Database, draft_id: str) -> tuple[str, dict]:
    async with db.session() as session:
        draft = await session.get(CharacterDraft, draft_id)
        assert draft is not None
        return draft.status, deepcopy(draft.data)


def _message(result):
    assert result.handled and len(result.responses) == 1
    return result.responses[0]


def _button(message, label: str):
    return next(button for button in message.action_buttons if button.label == label)


def _expanded_registry(*, level: int, class_name: str, extra: int = 24) -> RulesRegistry:
    registry = deepcopy(get_registry())
    for index in range(extra):
        key = f"expanded_{'cantrip' if level == 0 else 'spell'}_{index:02d}"
        registry.spells[key] = SpellDef(
            definition_id=f"spell:{key}",
            name=key,
            name_en=f"Expanded {'Cantrip' if level == 0 else 'Spell'} {index:02d}",
            name_th_hint=f"Test choice {index:02d}",
            level=level,
            school="evocation",
            casting_time="action",
            range="60 ft",
            duration="instant",
            ux_category="test",
            mech_summary_th=f"Bounded test option {index:02d}",
            classes=[class_name],
        )
    return registry


async def test_resume_without_draft_and_help_advertises_recovery(db, provider):
    await build_world(db)
    admin, _, _ = _bridges(db, provider)

    no_draft = _message(await admin.handle(_inbound("!rv resume")))
    assert no_draft.kind == MessageKind.TABLE_NOTICE
    assert "!rv character" in no_draft.content

    help_message = _message(await admin.handle(_inbound("!rv")))
    help_text = "\n".join(field["value"] for field in help_message.data["fields"])
    assert "!rv resume" in help_text


async def test_resume_stage_a_replays_exact_saved_prompt_without_losing_answers(db, provider):
    world = await build_world(db)
    prompt = "EXACT ADAPTIVE PROMPT: What promise did the hero break?"
    saved = {
        "name": "Mira",
        "concept": "An oath-bound navigator",
        "origin": "A flooded city",
        "_last_prompt": prompt,
    }
    draft_id = await _add_draft(
        db, campaign_id=world.campaign_id, member_id=world.p1_member_id, data=saved, step=4
    )
    admin, _, _ = _bridges(db, provider)

    message = _message(await admin.handle(_inbound("!rv resume")))

    assert message.kind == MessageKind.CHARACTER_CREATION
    assert message.content == prompt
    status, after = await _draft_snapshot(db, draft_id)
    assert status == "ACTIVE"
    assert after == saved


async def test_resume_preserves_transient_class_and_ability_view_modes(db, provider):
    world = await build_world(db)
    class_draft = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data={"name": "One", "_build": {"step": "class", "class_show_all": True}},
    )
    ability_data = _complete_build_data("cantrips")
    ability_data["_build"].update({"step": "abilities", "ability_mode": "manual"})
    ability_data["_build"].pop("cantrips", None)
    ability_draft = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p2_member_id,
        data=ability_data,
    )
    admin, _, _ = _bridges(db, provider)

    class_screen = _message(await admin.handle(_inbound("!rv resume")))
    ability_screen = _message(await admin.handle(_inbound(
        "!rv resume", author="disc-p2", name="Player Two"
    )))

    assert len(class_screen.choices) == len(get_registry().selectable_classes)
    assert "ดูตัวเลือกทั้งหมด" not in class_screen.choices
    assert ability_screen.title == "จัดค่าเอง"
    assert "STR 8 DEX 12" in ability_screen.content
    _, class_saved = await _draft_snapshot(db, class_draft)
    _, ability_saved = await _draft_snapshot(db, ability_draft)
    assert class_saved["_build"]["class_show_all"] is True
    assert ability_saved["_build"]["ability_mode"] == "manual"


@pytest.mark.parametrize(
    ("step", "class_name", "selected"),
    [
        ("cantrips", "wizard", ["fire_bolt"]),
        ("prepared", "cleric", ["bless"]),
    ],
)
async def test_resume_rebuilds_exact_spell_step_controls(
    db, provider, step, class_name, selected
):
    world = await build_world(db)
    data = _complete_build_data(step, class_name=class_name, selected=selected)
    draft_id = await _add_draft(
        db, campaign_id=world.campaign_id, member_id=world.p1_member_id, data=data
    )
    admin, _, _ = _bridges(db, provider)

    message = _message(await admin.handle(_inbound("!rv resume")))

    assert message.kind == MessageKind.CHARACTER_CREATION
    assert message.select_menus
    assert message.action_buttons
    assert f"1 / {getattr(get_registry().get_class(class_name).spellcasting, 'cantrips_known' if step == 'cantrips' else 'prepared_count')}" in message.content
    emitted_values = [
        option.value for menu in message.select_menus for option in menu.options
    ] + [button.value for button in message.action_buttons]
    status, persisted = await _draft_snapshot(db, draft_id)
    token = persisted["_build"]["component_token"]
    assert isinstance(token, str) and token
    assert emitted_values and all(token in value for value in emitted_values)
    assert status == "ACTIVE"
    assert persisted["_build"]["step"] == step
    assert persisted["_build"][step] == selected


async def test_resume_survives_database_and_service_restart(tmp_path, provider):
    path = (tmp_path / "restart-recovery.db").as_posix()
    url = f"sqlite+aiosqlite:///{path}"
    first_db = Database(url, echo=False)
    await first_db.create_all()
    try:
        async with first_db.unit_of_work() as session:
            campaigns = CampaignService(session)
            campaign = await campaigns.create_campaign(
                name="Restart Test",
                discord_guild_id="restart-guild",
                game_channel_id="restart-channel",
                owner_discord_user_id="restart-owner",
                owner_display_name="Restart Owner",
            )
            await campaigns.activate_campaign(campaign.id)
            member = await campaigns.resolve_member(campaign.id, "restart-owner")
            assert member is not None
            draft = CharacterDraft(
                campaign_id=campaign.id,
                member_id=member.id,
                data=_complete_build_data("cantrips", selected=["fire_bolt"]),
            )
            session.add(draft)
            await session.flush()
            draft_id = draft.id
    finally:
        await first_db.dispose()

    restarted_db = Database(url, echo=False)
    try:
        # New DB wrapper, creation service, build-flow instance, and admin bridge.
        admin, _, _ = _bridges(restarted_db, provider)
        message = _message(await admin.handle(_inbound(
            "!rv resume",
            author="restart-owner",
            name="Restart Owner",
            channel="restart-channel",
            guild="restart-guild",
        )))
        assert message.kind == MessageKind.CHARACTER_CREATION
        assert "Fire Bolt" in message.content
        status, persisted = await _draft_snapshot(restarted_db, draft_id)
        assert status == "ACTIVE"
        assert persisted["_build"]["cantrips"] == ["fire_bolt"]
    finally:
        await restarted_db.dispose()


async def test_two_players_resume_only_their_own_distinct_drafts(db, provider):
    world = await build_world(db)
    await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data={"name": "One", "_last_prompt": "PLAYER-ONE-PROMPT"},
    )
    await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p2_member_id,
        data={"name": "Two", "_last_prompt": "PLAYER-TWO-PROMPT"},
    )
    admin, _, _ = _bridges(db, provider)

    one = _message(await admin.handle(_inbound("!rv resume")))
    two = _message(await admin.handle(_inbound(
        "!rv resume", author="disc-p2", name="Player Two"
    )))

    assert one.content == "PLAYER-ONE-PROMPT"
    assert two.content == "PLAYER-TWO-PROMPT"
    assert "PLAYER-TWO" not in one.content
    assert "PLAYER-ONE" not in two.content


async def test_resume_never_falls_back_to_another_members_draft(db, provider):
    world = await build_world(db)
    await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p2_member_id,
        data={"_last_prompt": "PRIVATE-PLAYER-TWO-PROMPT"},
    )
    admin, _, _ = _bridges(db, provider)

    message = _message(await admin.handle(_inbound("!rv resume")))

    assert message.kind == MessageKind.TABLE_NOTICE
    assert "!rv character" in message.content
    assert "PRIVATE-PLAYER-TWO" not in message.content


async def test_concurrent_character_start_keeps_one_active_member_draft(db, provider):
    world = await build_world(db)
    admin, _, _ = _bridges(db, provider)

    await asyncio.gather(
        admin.handle(_inbound("!rv character")),
        admin.handle(_inbound("!rv character")),
    )

    async with db.session() as session:
        drafts = list((await session.execute(select(CharacterDraft).where(
            CharacterDraft.campaign_id == world.campaign_id,
            CharacterDraft.member_id == world.p1_member_id,
            CharacterDraft.status == "ACTIVE",
        ))).scalars())
    assert len(drafts) == 1


async def test_large_cantrip_pool_paginates_through_real_routing_and_preserves_selection(
    db, provider
):
    world = await build_world(db)
    registry = _expanded_registry(level=0, class_name="wizard", extra=18)
    draft_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("cantrips"),
    )
    admin, game, _ = _bridges(db, provider, registry=registry)

    first = _message(await admin.handle(_inbound("!rv resume")))
    assert len(first.select_menus[0].options) == SPELL_PAGE_SIZE
    assert not _button(first, SPELL_NEXT).disabled
    assert _button(first, SPELL_PREVIOUS).disabled
    picked = first.select_menus[0].options[0]

    after_pick = _message(await game.handle_inbound(_inbound(picked.value)))
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["cantrips"] == [picked.value.rsplit(":", 1)[-1]]
    assert len(after_pick.select_menus) == 2  # pick menu + remove-selected menu

    second = _message(await game.handle_inbound(
        _inbound(_button(after_pick, SPELL_NEXT).value)
    ))
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["spell_pages"]["cantrips"] == 1
    assert 1 <= len(second.select_menus[0].options) <= SPELL_PAGE_SIZE
    assert not _button(second, SPELL_PREVIOUS).disabled
    assert _button(second, SPELL_NEXT).disabled
    assert registry.get_spell(persisted["_build"]["cantrips"][0]).name_th_hint in second.content

    last_boundary = _message(await game.handle_inbound(
        _inbound(_button(second, SPELL_NEXT).value)
    ))
    assert last_boundary.content.startswith("⚠️")
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["spell_pages"]["cantrips"] == 1

    back_on_first = _message(await game.handle_inbound(
        _inbound(_button(last_boundary, SPELL_PREVIOUS).value)
    ))
    assert any(
        option.value == picked.value and option.label.startswith("✅")
        for option in back_on_first.select_menus[0].options
    )
    first_boundary = _message(await game.handle_inbound(
        _inbound(_button(back_on_first, SPELL_PREVIOUS).value)
    ))
    assert first_boundary.content.startswith("⚠️")
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["spell_pages"]["cantrips"] == 0

    remove_menu = next(
        menu for menu in first_boundary.select_menus if "remove" in menu.custom_id
    )
    after_remove = _message(await game.handle_inbound(
        _inbound(remove_menu.options[0].value)
    ))
    assert len(after_remove.select_menus) == 1
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["cantrips"] == []


async def test_large_prepared_spell_pool_keeps_controls_on_every_page(db, provider):
    world = await build_world(db)
    registry = _expanded_registry(level=1, class_name="cleric")
    draft_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("prepared", class_name="cleric", selected=["bless"]),
    )
    admin, game, _ = _bridges(db, provider, registry=registry)

    first = _message(await admin.handle(_inbound("!rv resume")))
    assert len(first.select_menus[0].options) == SPELL_PAGE_SIZE
    assert first.action_buttons
    second = _message(await game.handle_inbound(
        _inbound(_button(first, SPELL_NEXT).value)
    ))

    assert second.select_menus[0].options
    assert second.action_buttons
    assert _button(second, SPELL_NEXT).disabled
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["prepared"] == ["bless"]
    assert persisted["_build"]["spell_pages"]["prepared"] == 1


async def test_spell_selection_rejects_duplicate_illegal_early_overflow_and_stale_step(
    db, provider
):
    world = await build_world(db)
    registry = _expanded_registry(level=0, class_name="wizard")
    draft_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("cantrips"),
    )
    admin, game, _ = _bridges(db, provider, registry=registry)
    current = _message(await admin.handle(_inbound("!rv resume")))
    first_four = [option.value for option in current.select_menus[0].options[:4]]

    current = _message(await game.handle_inbound(_inbound(first_four[0])))
    duplicate = _message(await game.handle_inbound(_inbound(first_four[0])))
    assert duplicate.content.startswith("⚠️")
    _, persisted = await _draft_snapshot(db, draft_id)
    assert len(persisted["_build"]["cantrips"]) == 1

    # A forged value for a real spell outside the wizard's legal pool is rejected.
    illegal_value = first_four[0].rsplit(":", 1)[0] + ":sacred_flame"
    illegal = _message(await game.handle_inbound(_inbound(illegal_value)))
    assert illegal.content.startswith("⚠️")
    _, persisted = await _draft_snapshot(db, draft_id)
    assert len(persisted["_build"]["cantrips"]) == 1

    early = _message(await game.handle_inbound(
        _inbound(_button(illegal, SPELL_CONFIRM).value)
    ))
    assert early.content.startswith("⚠️")
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["step"] == "cantrips"

    current = _message(await game.handle_inbound(_inbound(first_four[1])))
    current = _message(await game.handle_inbound(_inbound(first_four[2])))
    assert not _button(current, SPELL_CONFIRM).disabled
    overflow = _message(await game.handle_inbound(_inbound(first_four[3])))
    assert overflow.content.startswith("⚠️")
    _, persisted = await _draft_snapshot(db, draft_id)
    assert len(persisted["_build"]["cantrips"]) == 3

    book = _message(await game.handle_inbound(
        _inbound(_button(overflow, SPELL_CONFIRM).value)
    ))
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["step"] == "book"
    assert book.select_menus

    # A selection emitted for the prior cantrip step cannot mutate the spellbook.
    stale_step = _message(await game.handle_inbound(_inbound(first_four[3])))
    assert stale_step.content.startswith("⚠️")
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["step"] == "book"
    assert persisted["_build"]["book"] == []


async def test_typed_fallback_updates_saved_draft_and_invalid_name_explains_suggestions(
    db, provider
):
    world = await build_world(db)
    draft_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("cantrips"),
    )
    admin, game, _ = _bridges(db, provider)
    await admin.handle(_inbound("!rv resume"))

    invalid = _message(await game.handle_inbound(_inbound("fier bolt")))
    assert invalid.content.startswith("⚠️")
    assert "fier bolt" in invalid.content
    assert "Fire Bolt" in invalid.content
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["cantrips"] == []

    selected = _message(await game.handle_inbound(_inbound("  FIRE   BOLT  ")))
    assert selected.content.startswith("⚠️")  # positive selection notice, not a silent redraw
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["cantrips"] == ["fire_bolt"]


async def test_prepared_typed_fallback_uses_thai_name_and_configured_alias(db, provider):
    world = await build_world(db)
    draft_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("prepared", class_name="cleric"),
    )
    admin, game, _ = _bridges(db, provider)
    await admin.handle(_inbound("!rv resume"))

    await game.handle_inbound(_inbound("โล่แห่งศรัทธา"))
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["prepared"] == ["shield_of_faith"]

    await game.handle_inbound(_inbound("remove โล่แห่งศรัทธา"))
    await game.handle_inbound(_inbound("faith shield"))
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["prepared"] == ["shield_of_faith"]


async def test_concurrent_clicks_on_one_draft_are_serialized_without_lost_selection(
    db, provider
):
    world = await build_world(db)
    draft_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("cantrips"),
    )
    admin, game, _ = _bridges(db, provider)
    message = _message(await admin.handle(_inbound("!rv resume")))
    first, second = [option.value for option in message.select_menus[0].options[:2]]

    await asyncio.gather(
        game.handle_inbound(_inbound(first)),
        game.handle_inbound(_inbound(second)),
    )

    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["cantrips"] == [
        first.rsplit(":", 1)[-1],
        second.rsplit(":", 1)[-1],
    ]


async def test_back_preserves_all_earlier_answers_and_spell_selections(db, provider):
    world = await build_world(db)
    original = _complete_build_data("cantrips", selected=["fire_bolt"])
    draft_id = await _add_draft(
        db, campaign_id=world.campaign_id, member_id=world.p1_member_id, data=original
    )
    admin, game, _ = _bridges(db, provider)
    spell_message = _message(await admin.handle(_inbound("!rv resume")))

    result = _message(await game.handle_inbound(
        _inbound(_button(spell_message, SPELL_BACK).value)
    ))

    assert result.kind == MessageKind.CHARACTER_CREATION
    status, persisted = await _draft_snapshot(db, draft_id)
    assert status == "ACTIVE"
    assert persisted["name"] == original["name"]
    assert persisted["concept"] == original["concept"]
    assert persisted["origin"] == original["origin"]
    assert persisted["desire"] == original["desire"]
    assert persisted["_build"]["scores"] == original["_build"]["scores"]
    assert persisted["_build"]["cantrips"] == ["fire_bolt"]
    assert persisted["_build"]["step"] == "skills"
    assert persisted["_build"]["_return_spell_step"] == "cantrips"


async def test_cancel_and_stale_cross_player_components_are_draft_scoped(db, provider):
    world = await build_world(db)
    first_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("cantrips"),
    )
    second_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p2_member_id,
        data=_complete_build_data("cantrips"),
    )
    admin, game, _ = _bridges(db, provider)
    first = _message(await admin.handle(_inbound("!rv resume")))
    second = _message(await admin.handle(_inbound(
        "!rv resume", author="disc-p2", name="Player Two"
    )))
    _, first_data = await _draft_snapshot(db, first_id)
    _, second_data = await _draft_snapshot(db, second_id)
    first_token = first_data["_build"]["component_token"]
    second_token = second_data["_build"]["component_token"]
    assert first_token != second_token

    stale_pick = first.select_menus[0].options[0].value
    rejected_pick = _message(await game.handle_inbound(_inbound(
        stale_pick, author="disc-p2", name="Player Two"
    )))
    assert rejected_pick.content.startswith("⚠️")
    _, second_data = await _draft_snapshot(db, second_id)
    assert second_data["_build"]["cantrips"] == []

    stale_cancel = _button(first, SPELL_CANCEL).value
    rejected_cancel = _message(await game.handle_inbound(_inbound(
        stale_cancel, author="disc-p2", name="Player Two"
    )))
    assert rejected_cancel.content.startswith("⚠️")
    second_status, _ = await _draft_snapshot(db, second_id)
    assert second_status == "ACTIVE"

    cancelled = _message(await game.handle_inbound(
        _inbound(_button(first, SPELL_CANCEL).value)
    ))
    assert cancelled.kind == MessageKind.TABLE_NOTICE
    first_status, _ = await _draft_snapshot(db, first_id)
    second_status, _ = await _draft_snapshot(db, second_id)
    assert first_status == "CANCELLED"
    assert second_status == "ACTIVE"

    # The unaffected player can still resume the same saved step.
    resumed_second = _message(await admin.handle(_inbound(
        "!rv resume", author="disc-p2", name="Player Two"
    )))
    assert resumed_second.select_menus
    assert second_token in resumed_second.select_menus[0].options[0].value
    assert first_token not in resumed_second.select_menus[0].options[0].value


async def test_foreign_spell_component_cannot_mutate_another_players_stage_a_draft(
    db, provider
):
    world = await build_world(db)
    first_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("cantrips"),
    )
    stage_a = {"name": "Still Thinking", "_last_prompt": "Tell me more"}
    second_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p2_member_id,
        data=stage_a,
    )
    admin, game, _ = _bridges(db, provider)
    first = _message(await admin.handle(_inbound("!rv resume")))

    rejected = _message(await game.handle_inbound(_inbound(
        first.select_menus[0].options[0].value,
        author="disc-p2",
        name="Player Two",
    )))

    assert "ปุ่มนี้ใช้ไม่ได้แล้ว" in (rejected.title or "")
    assert "!rv resume" in rejected.content
    first_status, _ = await _draft_snapshot(db, first_id)
    second_status, second_data = await _draft_snapshot(db, second_id)
    assert first_status == second_status == "ACTIVE"
    assert second_data == stage_a


async def test_natural_prose_containing_cancel_substring_does_not_cancel_draft(db, provider):
    world = await build_world(db)
    draft_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data={"_last_prompt": "เล่าอดีตของเจ้า"},
    )
    _, game, _ = _bridges(db, provider)

    result = await game.handle_inbound(_inbound(
        "หลังโรงเรียนเลิก ฉันมักกลับไปฝึกดาบกับพี่สาว"
    ))

    assert result.handled
    status, _ = await _draft_snapshot(db, draft_id)
    assert status == "ACTIVE"


async def test_empty_required_spell_pool_stops_with_diagnostic_instead_of_looping(
    db, provider
):
    world = await build_world(db)
    registry = deepcopy(get_registry())
    for key, spell in list(registry.spells.items()):
        if spell.level == 0 and "wizard" in spell.classes:
            registry.spells[key] = spell.model_copy(
                update={"classes": [name for name in spell.classes if name != "wizard"]}
            )
    draft_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("cantrips"),
    )
    admin, _, _ = _bridges(db, provider, registry=registry)

    message = _message(await admin.handle(_inbound("!rv resume")))

    assert message.kind == MessageKind.TECHNICAL_ERROR
    assert "class=wizard" in message.content
    assert "pool=cantrips" in message.content
    assert "legal_count=0" in message.content
    assert "rules_content_version=" in message.content
    status, persisted = await _draft_snapshot(db, draft_id)
    assert status == "ACTIVE"
    assert persisted["_build"]["step"] == "cantrips"


@pytest.mark.parametrize(
    "selected",
    [
        ["fire_bolt", "fire_bolt", "ray_of_frost"],
        ["fire_bolt", "ray_of_frost", "sacred_flame"],
    ],
)
async def test_corrupt_exact_count_selection_cannot_confirm(db, provider, selected):
    world = await build_world(db)
    draft_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("cantrips", selected=selected),
    )
    admin, game, _ = _bridges(db, provider)

    resumed = _message(await admin.handle(_inbound("!rv resume")))
    assert resumed.kind == MessageKind.TECHNICAL_ERROR
    confirmed = _message(await game.handle_inbound(_inbound(SPELL_CONFIRM)))
    assert confirmed.kind == MessageKind.TECHNICAL_ERROR
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["step"] == "cantrips"
    assert persisted["_build"]["cantrips"] == selected


async def test_prepared_step_rejects_off_class_spell_in_persisted_wizard_book(db, provider):
    world = await build_world(db)
    data = _complete_build_data("prepared")
    data["_build"]["book"][-1] = "sacred_flame"
    draft_id = await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=data,
    )
    admin, _, _ = _bridges(db, provider)

    message = _message(await admin.handle(_inbound("!rv resume")))

    assert message.kind == MessageKind.TECHNICAL_ERROR
    assert "pool=book" in message.content
    _, persisted = await _draft_snapshot(db, draft_id)
    assert persisted["_build"]["step"] == "prepared"


async def test_discord_adapter_keeps_paginated_components_inside_platform_limits(
    db, provider
):
    world = await build_world(db)
    registry = _expanded_registry(level=0, class_name="wizard", extra=40)
    await _add_draft(
        db,
        campaign_id=world.campaign_id,
        member_id=world.p1_member_id,
        data=_complete_build_data("cantrips", selected=["fire_bolt"]),
    )
    admin, _, _ = _bridges(db, provider, registry=registry)
    message = _message(await admin.handle(_inbound("!rv resume")))

    async def on_choice(_interaction, _value):
        return None

    view = ChoiceView(
        message.choices,
        on_choice,
        select_menus=message.select_menus,
        action_buttons=message.action_buttons,
    )
    selects = [child for child in view.children if isinstance(child, discord.ui.Select)]

    assert selects
    assert len(message.content) <= 4000
    assert len(view.children) <= 25
    assert len(view.to_components()) <= 5
    assert all(1 <= len(select.options) <= 25 for select in selects)
    assert all(len(select.custom_id) <= 100 for select in selects)
    assert all(
        len(option.label) <= 100 and len(option.value) <= 100
        for select in selects
        for option in select.options
    )
    assert all(
        len(child.label or "") <= 80
        for child in view.children
        if isinstance(child, discord.ui.Button)
    )
