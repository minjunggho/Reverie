from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.rules_content import get_registry
from app.services.campaigns.build_flow import BuildFlow
from app.services.campaigns.finalize import finalize_character
from tests.support.factories import build_world


async def test_registry_counts_and_subclasses_are_complete(db):
    reg = get_registry()
    assert len(reg.classes) == 12
    assert len(reg.subclasses) == 48
    assert len(reg.species) == 10
    assert len(reg.backgrounds) == 16

    wizard_subclasses = reg.subclasses_for_class("wizard")
    assert [s.name for s in wizard_subclasses] == [
        "abjurer",
        "diviner",
        "evoker",
        "illusionist",
    ]
    assert reg.get_subclass("abjurer").parent_class == "wizard"


async def test_class_selection_starts_subclass_preview_and_stores_planned_choice(db):
    flow = BuildFlow(db)
    draft = SimpleNamespace(id="draft-1")
    data = {"concept": "นักเรียนเวท", "name": "Iris"}

    async def _save(_draft, _data):
        data.update(_data)

    flow._save = _save

    await flow._on_class(draft, data, "จอมเวท (wizard)", "chan-1")
    assert data["_build"]["step"] == "subclass"
    assert data["_build"]["class"] == "wizard"

    await flow._on_subclass(draft, data, "Abjurer", "chan-1")
    assert data["_build"]["planned_subclass"] == "abjurer"
    assert data["_build"]["step"] == "species"


async def test_finalize_character_persists_planned_subclass(db):
    world = await build_world(db)
    draft = CharacterDraft(campaign_id=world.campaign_id, member_id=world.owner_member_id)
    async with db.unit_of_work() as s:
        s.add(draft)
        await s.flush()

    data = {
        "name": "Ari",
        "concept": "คนใจเย็น",
        "origin": "มาในเมือง",
        "desire": "อยากเรียนเวท",
        "fear": "ความสิ้นหวัง",
        "flaw": "เก็บตัว",
        "_build": {
            "class": "wizard",
            "species": "human",
            "background": "sage",
            "scores": {"str": 8, "dex": 12, "con": 13, "int": 15, "wis": 14, "cha": 10},
            "skills": ["arcana"],
            "asi": {"int": 1},
            "cantrips": [],
            "book": [],
            "prepared": [],
            "planned_subclass": "abjurer",
        },
    }

    await finalize_character(db, draft=draft, data=data, channel_id="chan-1")

    async with db.session() as s:
        char = (await s.execute(
            select(Character).where(Character.name == "Ari")
        )).scalar_one()
        assert char.planned_subclass == "abjurer"
