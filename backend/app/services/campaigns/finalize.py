"""Finalize a built character — ONE transaction, everything derived + provenanced.

HP/AC/saves/speed come from the derivation engine over the player's confirmed
choices. Every capability lands as a CharacterGrant with its source; spells carry
kind + prepared state; limited-use features get ResourceState rows. The AI decided
nothing here — it only recommended along the way.
"""
from __future__ import annotations

from app.discord_bridge.dto import BridgeResult, OutboundMessage
from app.models.character import Character
from app.models.character_draft import CharacterDraft
from app.models.progression import CharacterGrant, CharacterSpell
from app.presentation import MessageKind
from app.rules_content import get_registry
from app.services.campaigns.character_service import CharacterService
from app.services.campaigns.inventory_service import InventoryService
from app.tabletop.resources import ResourceEngine
from app.tabletop.rules.core import ABILITIES, ability_modifier
from app.tabletop.rules.derive import armor_class, max_hp_level_1

HOOK_KEYS = ("concept", "origin", "desire", "fear", "flaw", "connection", "appearance")


async def finalize_character(db, *, draft: CharacterDraft, data: dict,
                             channel_id: str) -> BridgeResult:
    reg = get_registry()
    b = data["_build"]
    cls = reg.get_class(b["class"])
    sp = reg.get_species(b["species"])
    bg = reg.get_background(b["background"])
    scores: dict[str, int] = b["scores"]
    name = data.get("name") or "นักผจญภัย"

    async with db.unit_of_work() as s:
        char = await CharacterService(s).create_character(
            member_id=draft.member_id, name=name,
            species=sp.name, char_class=cls.name,
            abilities=scores, proficiencies=[], set_active=True,
            max_hp=max_hp_level_1(cls.name, scores["con"], sp.name),
            ac=armor_class(cls.name, scores["dex"]),
        )
        char.background = bg.name
        char.hooks = {k: v for k, v in data.items() if k in HOOK_KEYS and v}
        char.appearance = data.get("appearance", "")
        char.languages = ["Common"]
        char.tool_proficiencies = [bg.tool_proficiency]
        char.expertise = list(b.get("expertise", []))

        # Skills with provenance.
        skills: list[str] = []
        grants: list[CharacterGrant] = []

        def add_skill(skill: str, source_type: str, source_key: str) -> None:
            if skill not in skills:
                skills.append(skill)
                grants.append(CharacterGrant(
                    character_id=char.id, grant_type="skill", key=skill,
                    name_th=reg.skills[skill].name_th,
                    source_type=source_type, source_key=source_key,
                ))

        for sk in bg.skill_proficiencies:
            add_skill(sk, "BACKGROUND", bg.definition_id)
        for sk in b.get("skills", []):
            add_skill(sk, "CLASS", cls.definition_id)
        for key, value in b.items():
            if key.startswith("species_skill:"):
                add_skill(value, "SPECIES", sp.definition_id)
        char.proficiencies = skills

        # Species traits (resistances/darkvision/etc. live in grant data).
        for trait in sp.traits:
            grants.append(CharacterGrant(
                character_id=char.id, grant_type="trait", key=trait.key,
                name_th=trait.name_th, source_type="SPECIES", source_key=sp.definition_id,
                data={"resistances": trait.resistances,
                      "darkvision": trait.darkvision} if (trait.resistances or trait.darkvision) else {},
            ))

        # Class features (+ their resources) and the background's Origin feat.
        engine = ResourceEngine(s)
        for feat in cls.features:
            grants.append(CharacterGrant(
                character_id=char.id, grant_type="feature", key=feat.key,
                name_th=feat.name_th, source_type="CLASS", source_key=cls.definition_id,
            ))
            if feat.resource_id:
                await engine.grant(char, feat.resource_id)
        grants.append(CharacterGrant(
            character_id=char.id, grant_type="feat", key=bg.origin_feat,
            name_th=bg.origin_feat, source_type="BACKGROUND", source_key=bg.definition_id,
            data={"note": "บันทึกไว้ — กลไกใช้งานในสไลซ์ถัดไป"},
        ))
        for g in grants:
            s.add(g)

        # Spells with provenance + prepared state; slots as a resource pool.
        sc = cls.spellcasting
        if sc is not None:
            prepared = set(b.get("prepared", []))
            for spell in b.get("cantrips", []):
                s.add(CharacterSpell(character_id=char.id, spell_key=spell,
                                     kind="cantrip", source_type="CLASS",
                                     source_key=cls.definition_id))
            if sc.spellbook_size > 0:
                for spell in b.get("book", []):
                    s.add(CharacterSpell(character_id=char.id, spell_key=spell,
                                         kind="book", prepared=spell in prepared,
                                         source_type="CLASS", source_key=cls.definition_id))
            else:
                for spell in prepared:
                    s.add(CharacterSpell(character_id=char.id, spell_key=spell,
                                         kind="known", prepared=True,
                                         source_type="CLASS", source_key=cls.definition_id))
            await engine.grant(char, "resource:spell_slots_1")

        gear = await InventoryService(s).grant_starting_gear(character=char)
        for item in bg.equipment_th:
            await InventoryService(s).grant(character_id=char.id, name=item,
                                            record_event=False)
            gear.append(item)

        row = await s.get(CharacterDraft, draft.id)
        row.status = "DONE"

        # --- reveal card ------------------------------------------------------
        score_line = "  ".join(
            f"{a.upper()} {scores[a]} ({ability_modifier(scores[a]):+d})" for a in ABILITIES
        )
        fields = [
            {"name": "ตัวตน", "value": f"{sp.name_th} · {cls.name_th} · {bg.name_th}", "inline": False},
            {"name": "❤️ HP / 🛡️ AC", "value": f"{char.max_hp} / {char.ac}", "inline": True},
            {"name": "ความเร็ว", "value": f"{char.speed} ฟุต", "inline": True},
            {"name": "ความสามารถ", "value": score_line, "inline": False},
            {"name": "ทักษะถนัด",
             "value": ", ".join(reg.skills[k].name_th for k in skills) or "—", "inline": False},
        ]
        if sc is not None:
            from app.tabletop.rules.derive import spellcasting_block

            blk = spellcasting_block(char)
            fields.append({"name": "การร่ายเวท",
                           "value": f"Save DC {blk['save_dc']} · โจมตีเวท +{blk['attack_bonus']}",
                           "inline": False})
        fields.append({"name": "🎒 สัมภาระ", "value": "\n".join(f"• {g}" for g in gear[:8]),
                       "inline": False})
        hook_lines = [f"• {data[k]}" for k in ("desire", "fear", "flaw", "connection")
                      if data.get(k)]
        if hook_lines:
            fields.append({"name": "สิ่งที่ติดตัวมา", "value": "\n".join(hook_lines),
                           "inline": False})

    summary = data.get("_summary") or data.get("concept", "")
    return BridgeResult(handled=True, state_mutated=True, responses=[OutboundMessage(
        channel_id, summary, kind=MessageKind.CHARACTER_REVEAL,
        title=f"🎭 {name}",
        data={"fields": fields,
              "footer": "!rv sheet · !rv spells · !rv inventory — ดูได้ทุกเมื่อ"},
    )])
