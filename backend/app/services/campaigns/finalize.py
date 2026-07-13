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
        # Claim the draft FIRST (ACTIVE -> DONE, guarded) inside this same
        # transaction: a concurrent duplicate finalize (double-delivered click on
        # another worker) gets rowcount 0 and stops — one draft, one character.
        # If anything below fails, the claim rolls back with it.
        from sqlalchemy import update as _update

        claimed = await s.execute(
            _update(CharacterDraft)
            .where(CharacterDraft.id == draft.id, CharacterDraft.status == "ACTIVE")
            .values(status="DONE")
        )
        if claimed.rowcount != 1:
            return BridgeResult(handled=True, responses=[OutboundMessage(
                channel_id,
                "ตัวละครจากแบบร่างนี้ถูกสร้างเสร็จไปแล้ว — ดูได้ด้วย `!rv sheet`",
                kind=MessageKind.TABLE_NOTICE,
            )])

        char = await CharacterService(s).create_character(
            member_id=draft.member_id, name=name,
            species=sp.name, char_class=cls.name,
            abilities=scores, proficiencies=[], set_active=True,
            max_hp=max_hp_level_1(cls.name, scores["con"], sp.name),
            ac=armor_class(cls.name, scores["dex"],
                           con_score=scores["con"], wis_score=scores["wis"]),
        )
        char.background = bg.name
        # A subclass chosen in Stage B is a NARRATIVE plan by default; it only
        # becomes mechanical (active) at the class's subclass level. For a class
        # that chooses its subclass at level 1, activate + grant it now.
        char.planned_subclass = b.get("planned_subclass") or None
        char.hooks = {k: v for k, v in data.items() if k in HOOK_KEYS and v}
        char.appearance = data.get("appearance", "")
        char.languages = ["Common"]

        # Preserve the COMPLETE player-authored text verbatim, plus the structured
        # identity extracted from it — never one at the expense of the other.
        from app.services.campaigns.identity import generate_seeds, merge_identity

        identity = merge_identity(data.get("identity") or {}, {})
        # Record narrative-vs-mechanical facts the build captured.
        if data.get("_narrative_class"):
            identity["class_intention"] = data["_narrative_class"]
        identity["mechanical_class"] = cls.name
        if b.get("narrative_ancestry"):
            identity["ancestry"] = b["narrative_ancestry"]
        identity["mechanical_ancestry"] = b.get("mechanical_ancestry") or sp.name
        # Reviewable evolution seeds: proposed, PENDING until campaign validates them.
        identity["seeds"] = [s.as_dict() for s in generate_seeds(identity)]
        char.origin_text = data.get("_origin_text", "") or data.get("appearance", "")
        char.identity = identity
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

        # Class features AT THIS LEVEL (+ their resources) and the Origin feat.
        # Only level-appropriate features are granted — a level-1 caster does not
        # get a level-2 resource (e.g. sorcerer Font of Magic); level_up grants the rest.
        engine = ResourceEngine(s)
        for feat in cls.features_at(char.level):
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
            # Grant the slot pools THIS class declares (wizard/bard/etc. →
            # spell_slots_1; warlock → pact_slots), not a hardcoded pool. Only slot
            # levels the character can actually use at their level are granted.
            for slot_level_str, rid in (sc.slot_resources or {}).items():
                if int(slot_level_str) <= (char.level + 1) // 2:   # 1st-level slots from L1
                    await engine.grant(char, rid)

        # Activate a level-1 subclass (if this class chooses one at creation).
        if cls.subclass_level <= 1 and b.get("planned_subclass"):
            from app.tabletop.progression import SubclassService

            try:
                await SubclassService(s).select_subclass(char, b["planned_subclass"])
            except Exception:  # noqa: BLE001 — a bad plan never blocks creation
                pass

        gear = await InventoryService(s).grant_starting_gear(character=char)
        for item in bg.equipment_th:
            await InventoryService(s).grant(character_id=char.id, name=item,
                                            record_event=False)
            gear.append(item)
        from app.services.economy import WalletService
        from app.services.economy.wallet_service import format_balances

        purse = await WalletService(s).grant_starting_funds(character=char)

        # --- reveal card ------------------------------------------------------
        score_line = "  ".join(
            f"{a.upper()} {scores[a]} ({ability_modifier(scores[a]):+d})" for a in ABILITIES
        )
        # Identity line keeps the fiction visible: a narrative class/ancestry shows
        # alongside the mechanical chassis it resolved to.
        identity_line = f"{sp.name_th} · {cls.name_th} · {bg.name_th}"
        if identity.get("class_intention") and identity["class_intention"] != cls.name:
            identity_line = f"{identity['class_intention']} ({cls.name_th}) · {sp.name_th} · {bg.name_th}"
        if b.get("narrative_ancestry"):
            identity_line = f"{b['narrative_ancestry']} · {cls.name_th} · {bg.name_th} · กลไก {sp.name_th}"
        fields = [
            {"name": "ตัวตน", "value": identity_line, "inline": False},
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
        if b.get("planned_subclass"):
            sub = reg.get_subclass(b["planned_subclass"])
            fields.append({"name": "Subclass แผนไว้", "value": sub.name_th, "inline": False})
        fields.append({"name": "🎒 สัมภาระ", "value": "\n".join(f"• {g}" for g in gear[:8]),
                       "inline": False})
        fields.append({"name": "💰 ถุงเงิน", "value": format_balances(purse), "inline": False})
        hook_lines = [f"• {data[k]}" for k in ("desire", "fear", "flaw", "connection")
                      if data.get(k)]
        if hook_lines:
            fields.append({"name": "สิ่งที่ติดตัวมา", "value": "\n".join(hook_lines),
                           "inline": False})
        # Reviewable evolution seeds — proposed, not yet canon; the DM may weave
        # them in as the campaign confirms them.
        seed_lines = [f"• {sd['text']}" for sd in (identity.get("seeds") or [])]
        if seed_lines:
            fields.append({"name": "🌱 เมล็ดพันธุ์เรื่องราว (ข้อเสนอ)",
                           "value": "\n".join(seed_lines), "inline": False})

    summary = data.get("_summary") or data.get("concept", "")
    return BridgeResult(handled=True, state_mutated=True, responses=[OutboundMessage(
        channel_id, summary, kind=MessageKind.CHARACTER_REVEAL,
        title=f"🎭 {name}",
        data={"fields": fields,
              "footer": "!rv sheet · !rv spells · !rv inventory — ดูได้ทุกเมื่อ"},
    )])
