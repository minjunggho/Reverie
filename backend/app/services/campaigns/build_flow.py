"""Stage B — build the rules character. AI RECOMMENDS · PLAYER CHOOSES (§19-25).

A deterministic, registry-driven choice walk. No LLM anywhere in this module: the
recommendations are ranked engine-side from the Stage-A concept and rendered with
each definition's own Thai pitch. Every mechanical decision — class, species,
background, ability arrangement, ASI, skills, expertise, cantrips, spellbook,
prepared spells — is a player choice with legal options and enforced counts.

State lives in draft.data["_build"]; the flow is resumable and cancellable.
"""
from __future__ import annotations

import re
import secrets
from math import ceil

from app.core.errors import RulesViolation
from app.core.logging import get_logger
from app.discord_bridge.dto import (
    ActionButton,
    BridgeResult,
    OutboundMessage,
    SelectMenu,
    SelectOption,
)
from app.models.character_draft import CharacterDraft
from app.presentation import MessageKind
from app.rules_content import STANDARD_ARRAY, get_registry
from app.tabletop.rules.core import ABILITIES, ability_modifier

log = get_logger(__name__)

SHOW_ALL = "ดูตัวเลือกทั้งหมด"
CONFIRM_BUILD = "✅ สร้างเลย"
RESTART_BUILD = "✏️ เริ่มส่วนกฎใหม่"
USE_RECOMMENDED = "ใช้แบบแนะนำ"
ARRANGE_MYSELF = "จัดเอง"
CONTINUE_TO_SPELLS = "กลับไปเลือกคาถาต่อ"

# Keep well below Discord's 25-option menu ceiling and leave embed room for
# summaries, selected state, and recovery guidance.
SPELL_PAGE_SIZE = 15
SPELL_PREVIOUS = "◀ ก่อนหน้า"
SPELL_NEXT = "ถัดไป ▶"
SPELL_CONFIRM = "✅ ยืนยันตัวเลือก"
SPELL_BACK = "↩ ย้อนกลับ"
SPELL_CANCEL = "✖ ยกเลิกการสร้าง"
_SPELL_COMPONENT_PREFIX = "rvspell"

_AB_TH = {"str": "STR พลัง", "dex": "DEX คล่องแคล่ว", "con": "CON อึด",
          "int": "INT ปัญญา", "wis": "WIS สังเกตการณ์", "cha": "CHA เสน่ห์"}


class BuildFlow:
    """Owns draft.data['_build']. The CreationFlowService delegates here."""

    def __init__(self, db) -> None:
        self.db = db
        self.reg = get_registry()

    # ---------- entry -----------------------------------------------------------
    async def start(self, draft: CharacterDraft, data: dict, channel_id: str) -> BridgeResult:
        data["_build"] = {
            "step": "class",
            "component_token": secrets.token_urlsafe(12),
        }
        await self._save(draft, data)
        intro = ("ต่อไปเป็นส่วนกฎเกม — ข้าจะอธิบายตัวเลือกที่เข้ากับตัวละคร "
                 "แต่เจ้าจะเป็นคนเลือกทั้งหมด\n\n")
        # Honor a stated-but-unsupported class in the fiction; propose the closest
        # supported chassis (still the player's explicit choice).
        narrative = data.get("_narrative_class")
        if narrative:
            hint_th = CLASS_TH.get(data.get("_class_hint", ""), data.get("_class_hint", ""))
            intro += (f"เจ้าอยากเล่นเป็น **{narrative}** — ในเนื้อเรื่องเป็นแบบนั้นได้เต็มที่ "
                      f"ตอนนี้กลไกยังไม่รองรับคลาสนั้นตรงๆ ข้าเลยเสนอ **{hint_th}** ที่ใกล้ที่สุด "
                      "เป็นตัวเลือกแรก — หรือเลือกอย่างอื่นก็ได้\n\n")
        return self._class_step(data, channel_id, intro=intro)

    async def handle(self, draft: CharacterDraft, data: dict, text: str,
                     channel_id: str) -> BridgeResult:
        data.setdefault("_build", {})
        build = data.get("_build") or {}
        step = build.get("step", "class")
        handler = getattr(self, f"_on_{step}", None)
        if handler is None:
            return self._diagnostic(
                channel_id, f"ไม่รู้จักขั้นตอนที่บันทึกไว้: {step!r}"
            )
        return await handler(draft, data, text.strip(), channel_id)

    def render(self, data: dict, channel_id: str) -> BridgeResult:
        """Render the exact persisted Stage-B step without mutating the draft."""
        build = data.get("_build") or {}
        step = build.get("step")
        try:
            if step == "class":
                return self._class_step(
                    data,
                    channel_id,
                    show_all=bool(build.get("class_show_all")),
                )
            if step == "subclass":
                return self._subclass_step(data, channel_id)
            if step == "ancestry_package":
                return self._ancestry_package_step(data, channel_id)
            if step == "species":
                return self._species_step(data, channel_id)
            if step == "background":
                return self._background_step(data, channel_id)
            if step == "abilities":
                if build.get("ability_mode") == "manual":
                    return self._manual_abilities_step(channel_id)
                return self._abilities_step(data, channel_id)
            if step == "asi":
                return self._asi_step(data, channel_id)
            if step == "skills":
                return self._skills_step(data, channel_id)
            if step == "species_skill":
                trait_key = build.get("species_skill_trait", "")
                species = self.reg.get_species(build["species"])
                trait = next((t for t in species.traits if t.key == trait_key), None)
                if trait is None:
                    return self._diagnostic(
                        channel_id,
                        f"ไม่พบตัวเลือกทักษะเผ่าที่บันทึกไว้: {trait_key!r}",
                    )
                return self._species_skill_step(data, channel_id, trait)
            if step == "expertise":
                return self._expertise_step(data, channel_id)
            if step in {"cantrips", "book", "prepared"}:
                return self._spell_selection_step(data, channel_id, key=step)
            if step == "review":
                return self._review_step(data, channel_id)
        except (KeyError, RulesViolation, TypeError, ValueError) as exc:
            return self._diagnostic(channel_id, str(exc))
        return self._diagnostic(
            channel_id, f"ไม่รู้จักขั้นตอนที่บันทึกไว้: {step!r}"
        )

    # ---------- step: class -----------------------------------------------------
    def _rank_classes(self, data: dict) -> list[str]:
        blob = " ".join(str(data.get(k, "")) for k in ("concept", "origin", "desire", "flaw"))
        scored = []
        for cls in self.reg.selectable_class_defs():
            name = cls.name
            hits = sum(1 for kw in cls.concept_keywords if kw in blob)
            scored.append((-hits, name))
        scored.sort()
        ranked = [name for _, name in scored]
        # An explicit class intention (stated by the player, or the proposed chassis
        # for an unsupported class) leads the recommendations — never buried.
        hint = data.get("_class_hint")
        if hint in ranked:
            ranked.remove(hint)
            ranked.insert(0, hint)
        return ranked

    def _class_step(self, data: dict, channel_id: str, intro: str = "",
                    show_all: bool = False) -> BridgeResult:
        ranked = self._rank_classes(data)
        shown = ranked if show_all else ranked[:3]
        lines, choices = [], []
        for i, name in enumerate(shown):
            cls = self.reg.get_class(name)
            tag = " ⭐ แนะนำ" if (not show_all and i == 0) else ""
            lines.append(f"**{cls.name_th} ({cls.name.title()})**{tag}\n{cls.pitch_th}")
            choices.append(f"{cls.name_th} ({cls.name})")
        if not show_all:
            choices.append(SHOW_ALL)
        return _card(channel_id, "เลือกเส้นทาง (Class)",
                     intro + "\n\n".join(lines), choices)

    async def _on_class(self, draft, data, text, channel_id) -> BridgeResult:
        if SHOW_ALL in text:
            data["_build"]["class_show_all"] = True
            await self._save(draft, data)
            return self._class_step(data, channel_id, show_all=True)
        picked = _match(text, {name: self.reg.classes[name]
                               for name in self.reg.selectable_classes})
        if picked is None:
            return self._class_step(data, channel_id,
                                    intro="เลือกจากปุ่ม หรือพิมพ์ชื่อ Class ได้เลย\n\n",
                                    show_all=bool(data["_build"].get("class_show_all")))
        data.setdefault("_build", {})
        if self.reg.subclasses_for_class(picked):
            data["_build"].update({"step": "subclass", "class": picked})
            await self._save(draft, data)
            return self._subclass_step(data, channel_id)
        data["_build"]["class"] = picked
        return await self._begin_ancestry(draft, data, channel_id)

    async def _begin_ancestry(self, draft, data, channel_id) -> BridgeResult:
        """A custom ancestry (Catfolk, a winged variant, …) can't just be a bundled
        species: its NARRATIVE appearance is preserved, but its MECHANICAL package
        must be chosen and owner-approved so an appearance never silently grants
        flight/resistance/etc. Everyone else goes straight to the species menu."""
        if data.get("_custom_ancestry"):
            data["_build"]["step"] = "ancestry_package"
            await self._save(draft, data)
            return self._ancestry_package_step(data, channel_id)
        data["_build"]["step"] = "species"
        await self._save(draft, data)
        return self._species_step(data, channel_id)

    # ---------- step: custom-ancestry mechanical package ----------------------------
    def _ancestry_package_step(self, data: dict, channel_id: str) -> BridgeResult:
        from app.services.campaigns.identity import suggested_base_for_custom

        ancestry = data.get("_custom_ancestry", "เผ่าพิเศษ")
        suggested = suggested_base_for_custom(ancestry)
        lines, choices = [], []
        for name, sp in self.reg.species.items():
            tag = " ⭐ ใกล้ที่สุด" if name == suggested else ""
            traits = " · ".join(t.name_th for t in sp.traits)
            lines.append(f"**{sp.name_th}**{tag}\n-# ชุดกลไก: {traits}")
            choices.append(f"{sp.name_th} ({sp.name})")
        body = (
            f"**{ancestry}** เป็นเผ่าที่เจ้าคิดขึ้นเอง — รูปลักษณ์และเรื่องราวของมันข้าเก็บไว้ครบ "
            "และจะปรากฏในเนื้อเรื่อง\n\n"
            "แต่ 'พลังตามกฎ' ต้องมาจากชุดสำเร็จที่ระบบรันได้ — เลือกชุดกลไกที่ใกล้กับภาพในหัวที่สุด "
            "(เช่น ปีกในรูปลักษณ์ไม่ได้แปลว่าบินได้ทันที เว้นแต่เจ้าของโต๊ะอนุมัติภายหลัง)\n\n"
            + "\n\n".join(lines)
        )
        return _card(channel_id, f"เผ่าที่ออกแบบเอง: {ancestry}", body, choices)

    async def _on_ancestry_package(self, draft, data, text, channel_id) -> BridgeResult:
        picked = _match(text, self.reg.species)
        if picked is None:
            return self._ancestry_package_step(data, channel_id)
        # Keep the narrative ancestry; the picked bundled species is only the
        # MECHANICAL chassis. Both are recorded for the review + finalize.
        data["_build"].update({"step": "background", "species": picked,
                               "mechanical_ancestry": picked,
                               "narrative_ancestry": data.get("_custom_ancestry")})
        await self._save(draft, data)
        return self._background_step(data, channel_id)

    # ---------- step: subclass ---------------------------------------------------
    def _subclass_step(self, data: dict, channel_id: str) -> BridgeResult:
        cls = self.reg.get_class(data["_build"]["class"])
        subclasses = self.reg.subclasses_for_class(cls.name)
        lines = [f"**{sub.name_th} ({sub.name})**\n{sub.pitch_th}" for sub in subclasses]
        choices = [f"{sub.name_th} ({sub.name})" for sub in subclasses]
        choices.append("ยังไม่เลือก (later)")
        return _card(channel_id, "เลือก Subclass (แผนไว้)",
                     f"สำหรับ {cls.name_th} มีตัวเลือก Subclass ที่ยังไม่เปิดใช้งานทางกลไกในเลเวล 1 — "
                     "เจ้าสามารถเลือกแผนไว้ตอนนี้หรือปล่อยไว้ก่อน\n\n" + "\n\n".join(lines),
                     choices)

    async def _on_subclass(self, draft, data, text, channel_id) -> BridgeResult:
        if any(word in text.lower() for word in ("ยังไม่เลือก", "later", "ไม่เลือก", "skip")):
            data["_build"]["planned_subclass"] = None
            return await self._begin_ancestry(draft, data, channel_id)
        picked = _match(text, {s.name: s for s in self.reg.subclasses_for_class(data["_build"]["class"])})
        if picked is None:
            return self._subclass_step(data, channel_id)
        data["_build"]["planned_subclass"] = picked
        return await self._begin_ancestry(draft, data, channel_id)

    # ---------- step: species ----------------------------------------------------
    def _species_step(self, data: dict, channel_id: str) -> BridgeResult:
        # An explicitly stated ancestry leads the recommendations — the flow never
        # falls back to Human just because nothing was inferred from prose.
        blob = " ".join(str(v) for v in data.values() if isinstance(v, str))
        hinted = data.get("_species_hint") or next(
            (n for n, th in (("elf", "เอลฟ์"), ("dwarf", "แคระ"),
                             ("halfling", "ฮาล์ฟลิง")) if th in blob), "human")
        if hinted not in self.reg.species:
            hinted = "human"
        lines, choices = [], []
        for name, sp in self.reg.species.items():
            tag = " ⭐ แนะนำ" if name == hinted else ""
            traits = " · ".join(t.name_th for t in sp.traits)
            lines.append(f"**{sp.name_th}**{tag}\n{sp.pitch_th}\n-# {traits}")
            choices.append(f"{sp.name_th} ({sp.name})")
        return _card(channel_id, "เลือกเผ่า (Species)",
                     "คำบรรยายอย่าง 'คนธรรมดา' ไม่ได้แปลว่าต้องเป็นมนุษย์เสมอไป —\n\n"
                     + "\n\n".join(lines), choices)

    async def _on_species(self, draft, data, text, channel_id) -> BridgeResult:
        picked = _match(text, self.reg.species)
        if picked is None:
            return self._species_step(data, channel_id)
        data["_build"].update({"step": "background", "species": picked})
        await self._save(draft, data)
        return self._background_step(data, channel_id)

    # ---------- step: background --------------------------------------------------
    _BG_HINT = {"sage": ("ตำรา", "เรียน", "ครู", "หนังสือ", "ศึกษา"),
                "criminal": ("โจร", "ขโมย", "ตรอก", "นอกกฎหมาย"),
                "soldier": ("ทหาร", "สงคราม", "กองทัพ", "รบ"),
                "acolyte": ("วัด", "ศรัทธา", "โบสถ์", "สวด")}
    _BG_BY_CLASS = {"wizard": "sage", "rogue": "criminal", "fighter": "soldier",
                    "cleric": "acolyte", "ranger": "soldier", "bard": "sage"}

    def _background_step(self, data: dict, channel_id: str) -> BridgeResult:
        blob = " ".join(str(v) for v in data.values() if isinstance(v, str))
        hinted = next((bg for bg, kws in self._BG_HINT.items()
                       if any(k in blob for k in kws)),
                      self._BG_BY_CLASS.get(data["_build"]["class"], "sage"))
        lines, choices = [], []
        for name, bg in self.reg.backgrounds.items():
            tag = " ⭐ แนะนำ" if name == hinted else ""
            skills = ", ".join(self.reg.skills[s].name_th for s in bg.skill_proficiencies)
            lines.append(f"**{bg.name_th}**{tag}\n{bg.pitch_th}\n-# ทักษะ: {skills} · Feat: {bg.origin_feat}")
            choices.append(f"{bg.name_th} ({bg.name})")
        return _card(
            channel_id, "เลือกภูมิหลังตามกฎ (Background)",
            "อันนี้คนละอย่างกับ 'เรื่องราวชีวิต' ที่เล่ามา — Background เป็นชุดความถนัด"
            "ตามกฎที่สะท้อนว่าตัวละครโตมากับอะไร\n\n" + "\n\n".join(lines), choices)

    async def _on_background(self, draft, data, text, channel_id) -> BridgeResult:
        picked = _match(text, self.reg.backgrounds)
        if picked is None:
            return self._background_step(data, channel_id)
        data["_build"].update({"step": "abilities", "background": picked})
        await self._save(draft, data)
        return self._abilities_step(data, channel_id)

    # ---------- step: ability scores (Standard Array) ------------------------------
    def _recommended_scores(self, data: dict) -> dict[str, int]:
        cls = self.reg.get_class(data["_build"]["class"])
        order = list(cls.primary_abilities) + [a for a in ABILITIES
                                               if a not in cls.primary_abilities]
        return {ab: STANDARD_ARRAY[i] for i, ab in enumerate(order)}

    def _abilities_step(self, data: dict, channel_id: str) -> BridgeResult:
        rec = self._recommended_scores(data)
        rec_line = "  ".join(f"{_AB_TH[a].split()[0]} {rec[a]}" for a in ABILITIES)
        body = (
            "ค่าความสามารถใช้ **Standard Array** — ชุดตัวเลขสมดุล เหมาะกับผู้เล่นใหม่: "
            f"{', '.join(map(str, STANDARD_ARRAY))}\n\n"
            f"สำหรับ {self.reg.get_class(data['_build']['class']).name_th} ข้าขอแนะนำ:\n"
            f"**{rec_line}**\n\n"
            "นี่เป็นเพียงคำแนะนำ — จะจัดเองก็ได้ "
            "(พิมพ์เช่น `STR 8 DEX 12 CON 13 INT 15 WIS 14 CHA 10`)"
        )
        return _card(channel_id, "ค่าความสามารถ", body, [USE_RECOMMENDED, ARRANGE_MYSELF])

    @staticmethod
    def _manual_abilities_step(channel_id: str) -> BridgeResult:
        return _card(
            channel_id,
            "จัดค่าเอง",
            "พิมพ์การจัดของเจ้า เช่น `STR 8 DEX 12 CON 13 INT 15 WIS 14 CHA 10`\n"
            f"ต้องใช้ตัวเลขชุดนี้ครบทุกตัว: {', '.join(map(str, STANDARD_ARRAY))}",
            [],
        )

    async def _on_abilities(self, draft, data, text, channel_id) -> BridgeResult:
        if USE_RECOMMENDED in text:
            scores = self._recommended_scores(data)
        elif ARRANGE_MYSELF in text:
            data["_build"]["ability_mode"] = "manual"
            await self._save(draft, data)
            return self._manual_abilities_step(channel_id)
        else:
            scores = _parse_scores(text)
            if scores is None:
                return _card(channel_id, "ยังอ่านไม่ออก",
                             "รูปแบบ: `STR 8 DEX 12 CON 13 INT 15 WIS 14 CHA 10` "
                             f"และต้องเป็นชุด {STANDARD_ARRAY} พอดี", [USE_RECOMMENDED])
        data["_build"].pop("ability_mode", None)
        data["_build"].update({"step": "asi", "scores": scores})
        await self._save(draft, data)
        return self._asi_step(data, channel_id)

    # ---------- step: background ASI (+2/+1 or +1+1+1) ------------------------------
    def _asi_options(self, data: dict) -> list[tuple[str, dict[str, int]]]:
        bg = self.reg.get_background(data["_build"]["background"])
        cls = self.reg.get_class(data["_build"]["class"])
        trio = bg.ability_options
        prio = [a for a in cls.primary_abilities if a in trio] + \
               [a for a in trio if a not in cls.primary_abilities]
        opts: list[tuple[str, dict[str, int]]] = []
        for two in prio:
            one = next(a for a in prio if a != two)
            opts.append((f"+2 {two.upper()}, +1 {one.upper()}", {two: 2, one: 1}))
        opts.append((f"+1 {trio[0].upper()}, +1 {trio[1].upper()}, +1 {trio[2].upper()}",
                     {a: 1 for a in trio}))
        return opts

    def _asi_step(self, data: dict, channel_id: str) -> BridgeResult:
        bg = self.reg.get_background(data["_build"]["background"])
        opts = self._asi_options(data)
        labels = [f"{label}{' ⭐ แนะนำ' if i == 0 else ''}" for i, (label, _) in enumerate(opts)]
        body = (f"Background **{bg.name_th}** ให้เพิ่มค่าความสามารถในกลุ่ม "
                f"{', '.join(a.upper() for a in bg.ability_options)} — เลือกแบบไหน?")
        return _card(channel_id, "โบนัสจากภูมิหลัง", body, labels)

    async def _on_asi(self, draft, data, text, channel_id) -> BridgeResult:
        for label, bumps in self._asi_options(data):
            if label.split(" ⭐")[0] in text:
                scores = dict(data["_build"]["scores"])
                for ab, inc in bumps.items():
                    scores[ab] = scores[ab] + inc
                data["_build"].update({"step": "skills", "scores": scores,
                                       "asi": bumps, "skills": []})
                await self._save(draft, data)
                return self._skills_step(data, channel_id)
        return self._asi_step(data, channel_id)

    # ---------- step: class skill choices (multi-tap) --------------------------------
    def _skill_options(self, data: dict) -> tuple[list[str], int]:
        cls = self.reg.get_class(data["_build"]["class"])
        bg = self.reg.get_background(data["_build"]["background"])
        opts = (list(self.reg.skills) if cls.skill_choices["options"] == "any"
                else list(cls.skill_choices["options"]))
        taken = set(bg.skill_proficiencies) | set(data["_build"].get("skills", []))
        return [s for s in opts if s not in taken], int(cls.skill_choices["count"])

    def _skills_step(self, data: dict, channel_id: str) -> BridgeResult:
        options, count = self._skill_options(data)
        chosen = data["_build"].get("skills", [])
        bg = self.reg.get_background(data["_build"]["background"])
        lines = [f"**{self.reg.skills[s].name_th}** ({s}) — {self.reg.skills[s].explain_th}"
                 for s in options[:12]]
        body = (f"ได้จาก Background แล้ว: "
                f"{', '.join(self.reg.skills[s].name_th for s in bg.skill_proficiencies)}\n"
                f"เลือกทักษะจาก Class — **เลือกแล้ว {len(chosen)} / {count}**\n\n"
                + "\n".join(lines))
        choices = [f"{self.reg.skills[s].name_th} ({s})" for s in options]
        if len(chosen) >= count and data["_build"].get("_return_spell_step"):
            choices = [CONTINUE_TO_SPELLS]
        return _card(channel_id, "เลือกทักษะถนัด", body, choices)

    async def _on_skills(self, draft, data, text, channel_id) -> BridgeResult:
        options, count = self._skill_options(data)
        chosen = data["_build"].get("skills", [])
        if text == CONTINUE_TO_SPELLS and len(chosen) >= count:
            return await self._resume_spell_after_back(draft, data, channel_id)
        if len(chosen) >= count:
            return self._skills_step(data, channel_id)
        picked = _match(text, {s: None for s in options})
        if picked is not None:
            data["_build"]["skills"].append(picked)
        chosen = data["_build"]["skills"]
        if len(chosen) < count:
            await self._save(draft, data)
            return self._skills_step(data, channel_id)
        return await self._after_skills(draft, data, channel_id)

    async def _after_skills(self, draft, data, channel_id) -> BridgeResult:
        # Species trait skill choices (Human Skillful / Elf Keen Senses) queue next.
        sp = self.reg.get_species(data["_build"]["species"])
        pending = [t for t in sp.traits if t.skill_choice
                   and f"species_skill:{t.key}" not in data["_build"]]
        if pending:
            data["_build"]["step"] = "species_skill"
            data["_build"]["species_skill_trait"] = pending[0].key
            await self._save(draft, data)
            return self._species_skill_step(data, channel_id, pending[0])
        # Rogue Expertise at L1.
        cls = self.reg.get_class(data["_build"]["class"])
        exp_feature = next((f for f in cls.features if f.expertise_choice), None)
        if exp_feature and "expertise" not in data["_build"]:
            data["_build"].update({"step": "expertise", "expertise": []})
            await self._save(draft, data)
            return self._expertise_step(data, channel_id)
        return await self._to_spells_or_review(draft, data, channel_id)

    def _species_skill_step(self, data, channel_id, trait) -> BridgeResult:
        opts = (list(self.reg.skills) if trait.skill_choice["options"] == "any"
                else list(trait.skill_choice["options"]))
        taken = set(self._all_skills_so_far(data))
        opts = [s for s in opts if s not in taken]
        choices = [f"{self.reg.skills[s].name_th} ({s})" for s in opts]
        if (data["_build"].get(f"species_skill:{trait.key}")
                and data["_build"].get("_return_spell_step")):
            choices = [CONTINUE_TO_SPELLS]
        return _card(channel_id, f"{trait.name_th} — เลือกทักษะเพิ่ม",
                     trait.summary_th, choices)

    async def _on_species_skill(self, draft, data, text, channel_id) -> BridgeResult:
        trait_key = data["_build"].get("species_skill_trait", "")
        if (text == CONTINUE_TO_SPELLS
                and data["_build"].get(f"species_skill:{trait_key}")):
            return await self._resume_spell_after_back(draft, data, channel_id)
        picked = _match(text, self.reg.skills)
        if picked is None:
            sp = self.reg.get_species(data["_build"]["species"])
            trait = next(t for t in sp.traits if t.key == trait_key)
            return self._species_skill_step(data, channel_id, trait)
        data["_build"][f"species_skill:{trait_key}"] = picked
        await self._save(draft, data)
        if data["_build"].get("_return_spell_step"):
            return await self._resume_spell_after_back(draft, data, channel_id)
        return await self._after_skills(draft, data, channel_id)

    def _expertise_step(self, data, channel_id) -> BridgeResult:
        chosen = data["_build"].get("expertise", [])
        opts = [s for s in self._all_skills_so_far(data) if s not in chosen]
        choices = [f"{self.reg.skills[s].name_th} ({s})" for s in opts]
        if len(chosen) >= 2 and data["_build"].get("_return_spell_step"):
            choices = [CONTINUE_TO_SPELLS]
        return _card(channel_id, "ความเชี่ยวชาญ (Expertise)",
                     f"เลือกทักษะที่ถนัดเป็นพิเศษ — โบนัสความถนัดคูณสอง\n"
                     f"**เลือกแล้ว {len(chosen)} / 2**",
                     choices)

    async def _on_expertise(self, draft, data, text, channel_id) -> BridgeResult:
        opts = self._all_skills_so_far(data)
        chosen = data["_build"].get("expertise", [])
        if (text == CONTINUE_TO_SPELLS
                and len(chosen) >= 2):
            return await self._resume_spell_after_back(draft, data, channel_id)
        if len(chosen) >= 2:
            return self._expertise_step(data, channel_id)
        picked = _match(text, {s: None for s in opts})
        if picked and picked not in data["_build"]["expertise"]:
            data["_build"]["expertise"].append(picked)
        if len(data["_build"]["expertise"]) < 2:
            await self._save(draft, data)
            return self._expertise_step(data, channel_id)
        return await self._to_spells_or_review(draft, data, channel_id)

    # ---------- steps: cantrips / spellbook / prepared ---------------------------------
    async def _to_spells_or_review(self, draft, data, channel_id) -> BridgeResult:
        cls = self.reg.get_class(data["_build"]["class"])
        sc = cls.spellcasting
        if sc and sc.cantrips_known > 0 and "cantrips" not in data["_build"]:
            return await self._begin_spell_step(draft, data, channel_id, "cantrips")
        if sc and sc.spellbook_size > 0 and "book" not in data["_build"]:
            return await self._begin_spell_step(draft, data, channel_id, "book")
        if sc and sc.prepared_count > 0 and "prepared" not in data["_build"]:
            return await self._begin_spell_step(draft, data, channel_id, "prepared")
        data["_build"]["step"] = "review"
        await self._save(draft, data)
        return self._review_step(data, channel_id)

    async def _begin_spell_step(
        self, draft, data: dict, channel_id: str, key: str
    ) -> BridgeResult:
        build = data["_build"]
        build.setdefault(key, [])
        pages = dict(build.get("spell_pages") or {})
        pages.setdefault(key, 0)
        build["spell_pages"] = pages
        build["step"] = key
        await self._save(draft, data)
        return self._spell_selection_step(data, channel_id, key=key)

    def _spell_state(self, data: dict, key: str) -> tuple[str, str, list[str], int]:
        build = data["_build"]
        cls = self.reg.get_class(build["class"])
        sc = cls.spellcasting
        if sc is None:
            raise RulesViolation(f"class={cls.name}; pool={key}; คลาสนี้ไม่มีรายการคาถา")

        if key == "cantrips":
            title = "เลือกคาถาประจำตัว (Cantrips)"
            intro = "เลือก Cantrip ที่ตัวละครใช้ได้โดยไม่เสียช่องเวท"
            source = [s.name for s in self.reg.spells_for_class(sc.spell_list, 0)]
            required = sc.cantrips_known
        elif key == "book":
            title = "คัดคาถาลงตำรา (Spellbook)"
            intro = "เลือกคาถาเลเวล 1 ที่จดไว้ในตำราตั้งแต่เริ่มต้น"
            source = [s.name for s in self.reg.spells_for_class(sc.spell_list, 1)]
            required = sc.spellbook_size
        elif key == "prepared":
            title = "เตรียมคาถา (Prepared / Known)"
            intro = "เลือกคาถาเลเวล 1 ที่พร้อมใช้เมื่อการผจญภัยเริ่มขึ้น"
            if sc.spellbook_size > 0:
                source = list(build.get("book") or [])
                legal_for_class = {
                    spell.name
                    for spell in self.reg.spells_for_class(sc.spell_list, 1)
                }
                illegal_book = [spell for spell in source if spell not in legal_for_class]
                if len(source) != len(set(source)) or illegal_book:
                    raise RulesViolation(
                        f"class={cls.name}; pool=book; invalid choices={illegal_book or source!r}; "
                        "expected unique class-legal spellbook choices"
                    )
                if len(source) != sc.spellbook_size:
                    raise RulesViolation(
                        f"class={cls.name}; pool=book; selected_count={len(source)}; "
                        f"required_count={sc.spellbook_size}; expected an exact completed spellbook"
                    )
            else:
                source = [s.name for s in self.reg.spells_for_class(sc.spell_list, 1)]
            required = sc.prepared_count
        else:
            raise RulesViolation(f"unknown spell pool: {key!r}")

        if required <= 0:
            raise RulesViolation(
                f"class={cls.name}; pool={key}; required_count={required}; expected > 0"
            )
        if not source:
            raise RulesViolation(
                f"class={cls.name}; pool={key}; legal_count=0; "
                f"expected at least {required} legal choices; "
                f"rules_content_version={self.reg.rules_content_version}"
            )
        if len(source) < required:
            raise RulesViolation(
                f"class={cls.name}; pool={key}; legal_count={len(source)}; "
                f"required_count={required}; expected legal_count >= required_count; "
                f"rules_content_version={self.reg.rules_content_version}"
            )
        return title, intro, source, required

    def _spell_selection_step(
        self, data: dict, channel_id: str, *, key: str, notice: str = ""
    ) -> BridgeResult:
        try:
            title, intro, pool, required = self._spell_state(data, key)
        except (KeyError, RulesViolation, TypeError, ValueError) as exc:
            return self._diagnostic(channel_id, str(exc))

        build = data["_build"]
        chosen = list(build.get(key) or [])
        if len(chosen) != len(set(chosen)):
            return self._diagnostic(
                channel_id, f"pool={key}; ตัวเลือกที่บันทึกไว้ซ้ำกัน: {chosen!r}"
            )
        illegal = [spell_key for spell_key in chosen if spell_key not in pool]
        if illegal:
            return self._diagnostic(
                channel_id, f"pool={key}; พบคาถาที่คลาสนี้เลือกไม่ได้: {illegal!r}"
            )
        if len(chosen) > required:
            return self._diagnostic(
                channel_id,
                f"pool={key}; selected_count={len(chosen)}; required_count={required}",
            )

        page_count = max(1, ceil(len(pool) / SPELL_PAGE_SIZE))
        try:
            raw_page = int((build.get("spell_pages") or {}).get(key, 0))
        except (TypeError, ValueError):
            raw_page = 0
            notice = notice or "เลขหน้าที่บันทึกไว้ไม่ถูกต้อง จึงกลับมาหน้าแรก"
        page = min(max(raw_page, 0), page_count - 1)
        if page != raw_page:
            notice = notice or "จำนวนหน้าของตัวเลือกเปลี่ยนไป จึงเลื่อนไปหน้าที่ใกล้ที่สุด"
        page_pool = pool[page * SPELL_PAGE_SIZE:(page + 1) * SPELL_PAGE_SIZE]

        selected_text = ", ".join(
            self.reg.get_spell(spell_key).name_th_hint for spell_key in chosen
        ) or "ยังไม่ได้เลือก"
        lines: list[str] = []
        options: list[SelectOption] = []
        for spell_key in page_pool:
            spell = self.reg.get_spell(spell_key)
            selected = spell_key in chosen
            mark = "✅" if selected else "▫️"
            conc = " · ต้องเพ่งสมาธิ" if spell.concentration else ""
            lines.append(
                f"{mark} **{spell.name_th_hint}** ({spell.display_name_en}) — "
                f"{spell.mech_summary_th[:140]}{conc}"
            )
            options.append(SelectOption(
                label=f"{'✅ ' if selected else ''}{spell.name_th_hint} ({spell.display_name_en})",
                value=self._spell_component(data, key, "pick", spell_key),
                description=spell.mech_summary_th,
            ))

        prefix = f"⚠️ {notice}\n\n" if notice else ""
        body = (
            f"{prefix}{intro}\n"
            f"**เลือกแล้ว {len(chosen)} / {required}**\n"
            f"ที่เลือก: {selected_text}\n"
            f"หน้า **{page + 1} / {page_count}**\n\n"
            + "\n".join(lines)
            + "\n\nพิมพ์ชื่ออังกฤษหรือชื่อไทยได้เช่นกัน"
        )

        menus = [SelectMenu(
            custom_id=f"rv-spell-pick-{key}",
            placeholder=f"เลือกจากหน้า {page + 1}…",
            options=options,
        )]
        if chosen:
            menus.append(SelectMenu(
                custom_id=f"rv-spell-remove-{key}",
                placeholder="เอาคาถาที่เลือกไว้ออก…",
                options=[SelectOption(
                    label=f"เอา {self.reg.get_spell(spell_key).name_th_hint} ออก",
                    value=self._spell_component(data, key, "remove", spell_key),
                ) for spell_key in chosen[:25]],
            ))

        buttons = [
            ActionButton(
                label=SPELL_PREVIOUS,
                value=self._spell_component(data, key, "previous"),
                disabled=page == 0,
            ),
            ActionButton(
                label=f"หน้า {page + 1}/{page_count}",
                value=self._spell_component(data, key, "page"),
                disabled=True,
            ),
            ActionButton(
                label=SPELL_NEXT,
                value=self._spell_component(data, key, "next"),
                disabled=page >= page_count - 1,
            ),
            ActionButton(
                label=f"เลือก {len(chosen)}/{required}",
                value=self._spell_component(data, key, "count"),
                disabled=True,
            ),
            ActionButton(
                label=SPELL_CONFIRM,
                value=self._spell_component(data, key, "confirm"),
                style="success",
                disabled=len(chosen) != required,
            ),
            ActionButton(
                label=SPELL_BACK,
                value=self._spell_component(data, key, "back"),
            ),
            ActionButton(
                label=SPELL_CANCEL,
                value=self._spell_component(data, key, "cancel"),
                style="danger",
            ),
        ]
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id,
            body,
            kind=MessageKind.CHARACTER_CREATION,
            title=title,
            data={"footer": "ปุ่มหมดอายุเมื่อไร ใช้ !rv resume ได้เสมอ"},
            select_menus=menus,
            action_buttons=buttons,
        )])

    async def _on_cantrips(self, draft, data, text, channel_id) -> BridgeResult:
        return await self._on_spell_selection(
            draft, data, text, channel_id, key="cantrips"
        )

    async def _on_book(self, draft, data, text, channel_id) -> BridgeResult:
        return await self._on_spell_selection(draft, data, text, channel_id, key="book")

    async def _on_prepared(self, draft, data, text, channel_id) -> BridgeResult:
        return await self._on_spell_selection(
            draft, data, text, channel_id, key="prepared"
        )

    async def _on_spell_selection(
        self, draft, data: dict, text: str, channel_id: str, *, key: str
    ) -> BridgeResult:
        try:
            _, _, pool, required = self._spell_state(data, key)
        except (KeyError, RulesViolation, TypeError, ValueError) as exc:
            return self._diagnostic(channel_id, str(exc))

        build = data["_build"]
        chosen = list(build.get(key) or [])
        invalid_state = self._invalid_spell_selection(chosen, pool, required)
        if invalid_state:
            return self._diagnostic(channel_id, f"pool={key}; {invalid_state}")
        component = self._parse_spell_component(text)
        action = "pick"
        payload = text.strip()
        if component is not None:
            expected_token, expected_step, action, payload = component
            current_token = str(build.get("component_token") or "")
            if expected_token != current_token or expected_step != key:
                return self._spell_selection_step(
                    data,
                    channel_id,
                    key=key,
                    notice="ปุ่มนี้มาจากแบบร่างหรือหน้าก่อนและใช้กับขั้นตอนปัจจุบันไม่ได้",
                )
        elif text.strip() == SPELL_CONFIRM:
            action = "confirm"
        elif text.strip() == SPELL_BACK:
            action = "back"
        elif text.strip() == SPELL_CANCEL:
            action = "cancel"
        elif text.casefold().startswith("remove "):
            action, payload = "remove", text[7:].strip()

        if action in {"previous", "next"}:
            pages = dict(build.get("spell_pages") or {})
            page_count = max(1, ceil(len(pool) / SPELL_PAGE_SIZE))
            try:
                raw_current = int(pages.get(key, 0))
            except (TypeError, ValueError):
                raw_current = 0
            current = min(max(raw_current, 0), page_count - 1)
            wanted = current - 1 if action == "previous" else current + 1
            if wanted < 0 or wanted >= page_count:
                boundary = "หน้าแรก" if wanted < 0 else "หน้าสุดท้าย"
                return self._spell_selection_step(
                    data, channel_id, key=key, notice=f"ตอนนี้อยู่{boundary}แล้ว"
                )
            pages[key] = wanted
            build["spell_pages"] = pages
            await self._save(draft, data)
            return self._spell_selection_step(data, channel_id, key=key)

        if action == "confirm":
            if len(chosen) != required:
                return self._spell_selection_step(
                    data,
                    channel_id,
                    key=key,
                    notice=f"ต้องเลือกให้ครบ {required} รายการก่อนยืนยัน (ตอนนี้ {len(chosen)})",
                )
            return await self._advance_after_spell(draft, data, channel_id, key)
        if action == "back":
            return await self._back_from_spell(draft, data, channel_id, key)
        if action == "cancel":
            return await self._cancel_draft(draft, channel_id)
        if action in {"page", "count"}:
            return self._spell_selection_step(data, channel_id, key=key)
        if action not in {"pick", "remove"}:
            return self._spell_selection_step(
                data, channel_id, key=key, notice="ปุ่มนี้ไม่ใช่คำสั่งที่ใช้ได้ในหน้านี้"
            )

        global_resolution = self.reg.resolve_spell_name(payload)
        if global_resolution.ambiguous:
            names = self._spell_names(global_resolution.ambiguous_keys)
            return self._spell_selection_step(
                data,
                channel_id,
                key=key,
                notice=f"ชื่อนี้ตรงกับหลายคาถา: {names} — โปรดระบุชื่อเต็ม",
            )
        if global_resolution.key is not None and global_resolution.key not in pool:
            spell = self.reg.get_spell(global_resolution.key)
            return self._spell_selection_step(
                data,
                channel_id,
                key=key,
                notice=f"{spell.name_th_hint} ไม่ใช่ตัวเลือกที่ถูกกฎสำหรับคลาสนี้",
            )

        resolution = self.reg.resolve_spell_name(payload, allowed_keys=pool)
        if resolution.ambiguous:
            return self._spell_selection_step(
                data,
                channel_id,
                key=key,
                notice=("ชื่อนี้ยังไม่ชัดเจน: "
                        f"{self._spell_names(resolution.ambiguous_keys)}"),
            )
        spell_key = resolution.key
        if spell_key is None:
            suggestions = self._spell_names(resolution.suggestion_keys)
            hint = f" ใกล้เคียง: {suggestions}" if suggestions else ""
            return self._spell_selection_step(
                data,
                channel_id,
                key=key,
                notice=f"ไม่พบคาถาชื่อ “{payload}”.{hint}",
            )

        if action == "remove":
            if spell_key not in chosen:
                return self._spell_selection_step(
                    data, channel_id, key=key, notice="คาถานี้ยังไม่ได้ถูกเลือก จึงเอาออกไม่ได้"
                )
            chosen.remove(spell_key)
            build[key] = chosen
            await self._save(draft, data)
            return self._spell_selection_step(
                data,
                channel_id,
                key=key,
                notice=f"เอา {self.reg.get_spell(spell_key).name_th_hint} ออกแล้ว",
            )

        if spell_key in chosen:
            return self._spell_selection_step(
                data, channel_id, key=key, notice="คาถานี้ถูกเลือกไว้แล้ว — จะไม่เพิ่มซ้ำ"
            )
        if len(chosen) >= required:
            return self._spell_selection_step(
                data,
                channel_id,
                key=key,
                notice=(f"เลือกครบ {required} รายการแล้ว — เอารายการเดิมออกก่อน "
                        "หรือกดยืนยัน"),
            )
        chosen.append(spell_key)
        build[key] = chosen
        await self._save(draft, data)
        return self._spell_selection_step(
            data,
            channel_id,
            key=key,
            notice=f"เลือก {self.reg.get_spell(spell_key).name_th_hint} แล้ว",
        )

    async def _advance_after_spell(
        self, draft, data: dict, channel_id: str, key: str
    ) -> BridgeResult:
        build = data["_build"]
        return_step = build.pop("_return_spell_step", None)
        if return_step and return_step != key:
            notice = ""
            if return_step == "prepared" and key == "book":
                legal = set(build.get("book") or [])
                old_prepared = list(build.get("prepared") or [])
                build["prepared"] = [spell for spell in old_prepared if spell in legal]
                removed = [spell for spell in old_prepared if spell not in legal]
                if removed:
                    notice = (
                        "เอาคาถาที่ไม่ได้อยู่ในตำราแล้วออกจากชุดเตรียมไว้: "
                        + self._spell_names(removed)
                    )
            build["step"] = return_step
            await self._save(draft, data)
            return self._spell_selection_step(
                data, channel_id, key=return_step, notice=notice
            )

        cls = self.reg.get_class(build["class"])
        sc = cls.spellcasting
        if key == "cantrips" and sc and sc.spellbook_size > 0:
            return await self._begin_spell_step(draft, data, channel_id, "book")
        if key in {"cantrips", "book"} and sc and sc.prepared_count > 0:
            return await self._begin_spell_step(draft, data, channel_id, "prepared")
        build["step"] = "review"
        await self._save(draft, data)
        return self._review_step(data, channel_id)

    async def _back_from_spell(
        self, draft, data: dict, channel_id: str, key: str
    ) -> BridgeResult:
        build = data["_build"]
        cls = self.reg.get_class(build["class"])
        sc = cls.spellcasting
        previous: str | None = None
        if key == "prepared" and sc and sc.spellbook_size > 0 and "book" in build:
            previous = "book"
        elif key in {"prepared", "book"} and "cantrips" in build:
            previous = "cantrips"
        if previous is None:
            previous = self._pre_spell_step(data)

        build["_return_spell_step"] = key
        build["step"] = previous
        await self._save(draft, data)
        return self.render(data, channel_id)

    def _pre_spell_step(self, data: dict) -> str:
        build = data["_build"]
        if "expertise" in build:
            return "expertise"
        species = self.reg.get_species(build["species"])
        completed_traits = [
            trait for trait in species.traits
            if trait.skill_choice and f"species_skill:{trait.key}" in build
        ]
        if completed_traits:
            build["species_skill_trait"] = completed_traits[-1].key
            return "species_skill"
        return "skills"

    async def _resume_spell_after_back(
        self, draft, data: dict, channel_id: str
    ) -> BridgeResult:
        target = data["_build"].pop("_return_spell_step", None)
        if target not in {"cantrips", "book", "prepared"}:
            return self._diagnostic(channel_id, "ไม่พบขั้นตอนคาถาที่จะกลับไป")
        data["_build"]["step"] = target
        await self._save(draft, data)
        return self._spell_selection_step(data, channel_id, key=target)

    async def _cancel_draft(self, draft: CharacterDraft, channel_id: str) -> BridgeResult:
        from app.services.campaigns.draft_store import close_draft

        await close_draft(self.db, draft.id, status="CANCELLED")
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id,
            "ยกเลิกการสร้างตัวละครนี้แล้ว ข้อมูลของผู้เล่นคนอื่นไม่ถูกเปลี่ยนแปลง",
            kind=MessageKind.TABLE_NOTICE,
        )])

    @staticmethod
    def _spell_component(
        data: dict, key: str, action: str, payload: str = ""
    ) -> str:
        token = str((data.get("_build") or {}).get("component_token") or "unbound")
        suffix = f":{payload}" if payload else ""
        return f"{_SPELL_COMPONENT_PREFIX}:{token}:{key}:{action}{suffix}"

    @staticmethod
    def _parse_spell_component(value: str) -> tuple[str, str, str, str] | None:
        parts = (value or "").split(":", 4)
        if len(parts) < 4 or parts[0] != _SPELL_COMPONENT_PREFIX:
            return None
        payload = parts[4] if len(parts) == 5 else ""
        return parts[1], parts[2], parts[3], payload

    def _spell_names(self, keys) -> str:
        return ", ".join(
            f"{self.reg.get_spell(key).name_th_hint} ({self.reg.get_spell(key).display_name_en})"
            for key in keys
        )

    @staticmethod
    def _invalid_spell_selection(
        chosen: list[str], pool: list[str], required: int
    ) -> str | None:
        if len(chosen) != len(set(chosen)):
            return f"duplicate selections={chosen!r}; expected unique choices"
        illegal = [spell for spell in chosen if spell not in pool]
        if illegal:
            return f"illegal choices={illegal!r}; expected choices from the current legal pool"
        if len(chosen) > required:
            return (f"selected_count={len(chosen)}; required_count={required}; "
                    "expected selected_count <= required_count")
        return None

    # ---------- review + finalize --------------------------------------------------------
    def _review_step(self, data, channel_id) -> BridgeResult:
        b = data["_build"]
        cls = self.reg.get_class(b["class"])
        sp = self.reg.get_species(b["species"])
        bg = self.reg.get_background(b["background"])
        scores = b["scores"]
        score_line = "  ".join(
            f"{a.upper()} {scores[a]} ({ability_modifier(scores[a]):+d})" for a in ABILITIES
        )
        skills = self._all_skills_so_far(data)
        identity = data.get("identity") or {}

        # A sectioned review keeps identity (fiction) and mechanics visibly separate,
        # so the player sees that their writing was kept AND what the rules resolved to.
        lines: list[str] = [f"**{data.get('name', 'นักผจญภัย')}**"]

        # 1) mechanical ancestry — with narrative ancestry shown when they differ.
        ancestry_line = sp.name_th
        if b.get("narrative_ancestry"):
            ancestry_line = f"{b['narrative_ancestry']} (กลไก: {sp.name_th})"
        klass_line = cls.name_th
        if data.get("_narrative_class"):
            klass_line = f"{data['_narrative_class']} (กลไก: {cls.name_th})"
        lines.append(f"เผ่า · คลาส · ภูมิหลัง: {ancestry_line} · {klass_line} · {bg.name_th}")

        # 2) identity / appearance sections (only what the player supplied).
        appearance = identity.get("appearance") or data.get("appearance")
        if appearance:
            lines.append(f"\n__รูปลักษณ์__\n{appearance}")
        relationship_bits = [f"{lbl}: {identity[k]}" for k, lbl in
                             (("family", "ครอบครัว"), ("mentors", "ผู้ชี้ทาง"),
                              ("rivals", "คู่ปรับ"), ("connections", "คนสำคัญ"))
                             if identity.get(k)]
        if relationship_bits:
            lines.append("\n__ความสัมพันธ์__\n" + " · ".join(relationship_bits))
        drive_bits = [f"{lbl}: {identity[k]}" for k, lbl in
                      (("goals", "เป้าหมาย"), ("fears", "ความกลัว"),
                       ("ideals", "อุดมคติ"), ("bonds", "พันธะ"),
                       ("flaws", "จุดอ่อน"), ("secrets", "ความลับ"))
                      if identity.get(k)]
        if drive_bits:
            lines.append("\n__แรงขับ / ปม__\n" + " · ".join(drive_bits))

        # 3) mechanics section.
        mech = [f"\n__กลไก__", score_line,
                f"ทักษะถนัด: {', '.join(self.reg.skills[s].name_th for s in skills)}"]
        if b.get("expertise"):
            mech.append(f"เชี่ยวชาญพิเศษ: {', '.join(self.reg.skills[s].name_th for s in b['expertise'])}")
        if b.get("planned_subclass"):
            mech.append(f"Subclass แผนไว้: {self.reg.get_subclass(b['planned_subclass']).name_th}")
        if b.get("cantrips"):
            mech.append(f"Cantrips: {', '.join(self.reg.get_spell(s).name_th_hint for s in b['cantrips'])}")
        if b.get("book"):
            mech.append(f"ตำราคาถา: {', '.join(self.reg.get_spell(s).name_th_hint for s in b['book'])}")
        if b.get("prepared"):
            mech.append(f"เตรียมไว้: {', '.join(self.reg.get_spell(s).name_th_hint for s in b['prepared'])}")
        lines.extend(mech)

        # 4) reviewable story seeds — proposed, pending campaign validation.
        from app.services.campaigns.identity import generate_seeds

        seeds = generate_seeds(identity)
        if seeds:
            lines.append("\n__เมล็ดพันธุ์เรื่องราว (ข้อเสนอ — ยังไม่ผูกมัด)__")
            lines.extend(f"• {s.text}" for s in seeds)

        return _card(channel_id, "ตรวจทานครั้งสุดท้าย", "\n".join(lines),
                     [CONFIRM_BUILD, RESTART_BUILD])

    async def _on_review(self, draft, data, text, channel_id) -> BridgeResult:
        if RESTART_BUILD in text or text.startswith("✏"):
            return await self.start(draft, data, channel_id)
        if CONFIRM_BUILD in text or "สร้าง" in text or text.startswith("✅"):
            return await self._finalize(draft, data, channel_id)
        return self._review_step(data, channel_id)

    async def _finalize(self, draft: CharacterDraft, data: dict, channel_id: str) -> BridgeResult:
        from app.services.campaigns.finalize import finalize_character

        return await finalize_character(self.db, draft=draft, data=data,
                                        channel_id=channel_id)

    # ---------- utils -----------------------------------------------------------------
    @staticmethod
    def _diagnostic(channel_id: str, detail: str) -> BridgeResult:
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id,
            "ขั้นตอนนี้ไม่มีตัวเลือกที่ถูกกฎให้ดำเนินต่อ จึงหยุดไว้เพื่อไม่ให้ข้อมูลเสียหาย\n\n"
            f"รายละเอียด: `{detail}`\n"
            "แจ้งเจ้าของโต๊ะให้ตรวจ rules content แล้วใช้ `!rv resume` หลังแก้ไข",
            kind=MessageKind.TECHNICAL_ERROR,
            title="สร้างตัวละครต่อไม่ได้",
        )])

    def _all_skills_so_far(self, data: dict) -> list[str]:
        b = data["_build"]
        bg = self.reg.get_background(b["background"])
        out = list(bg.skill_proficiencies) + list(b.get("skills", []))
        out += [v for k, v in b.items() if k.startswith("species_skill:")]
        seen, uniq = set(), []
        for s in out:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq

    async def _save(self, draft: CharacterDraft, data: dict) -> None:
        from app.services.campaigns.draft_store import save_draft

        # Compare-and-update on draft.version — never a blind overwrite.
        await save_draft(self.db, draft, data)


# ---------- module helpers ------------------------------------------------------------

def _card(channel_id: str, title: str, body: str, choices: list[str]) -> BridgeResult:
    return BridgeResult(handled=True, responses=[OutboundMessage(
        channel_id, body, kind=MessageKind.CHARACTER_CREATION, title=title,
        choices=choices[:25],
        data={"footer": "พิมพ์ 'ยกเลิก' ได้ทุกเมื่อ"},
    )])


def _match(text: str, options: dict) -> str | None:
    """Map a button label or typed text back to an option key.

    Labels look like 'จอมเวท (wizard)' — match the parenthesized key first, then a
    bare key mention, longest key first so 'investigation' beats 'invest'."""
    t = text.lower()
    m = re.search(r"\(([a-z_ ]+)\)", t)
    if m and m.group(1).strip() in options:
        return m.group(1).strip()
    for key in sorted(options, key=len, reverse=True):
        if key in t:
            return key
    return None


_SCORE_RE = re.compile(r"(str|dex|con|int|wis|cha)\s*[:=]?\s*(\d{1,2})", re.I)


def _parse_scores(text: str) -> dict[str, int] | None:
    pairs = {a.lower(): int(v) for a, v in _SCORE_RE.findall(text)}
    if set(pairs) != set(ABILITIES):
        return None
    if sorted(pairs.values()) != sorted(STANDARD_ARRAY):
        return None
    return pairs
