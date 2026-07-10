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

from app.core.logging import get_logger
from app.discord_bridge.dto import BridgeResult, OutboundMessage
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

_AB_TH = {"str": "STR พลัง", "dex": "DEX คล่องแคล่ว", "con": "CON อึด",
          "int": "INT ปัญญา", "wis": "WIS สังเกตการณ์", "cha": "CHA เสน่ห์"}


class BuildFlow:
    """Owns draft.data['_build']. The CreationFlowService delegates here."""

    def __init__(self, db) -> None:
        self.db = db
        self.reg = get_registry()

    # ---------- entry -----------------------------------------------------------
    async def start(self, draft: CharacterDraft, data: dict, channel_id: str) -> BridgeResult:
        data["_build"] = {"step": "class"}
        await self._save(draft, data)
        intro = ("ต่อไปเป็นส่วนกฎเกม — ข้าจะอธิบายตัวเลือกที่เข้ากับตัวละคร "
                 "แต่เจ้าจะเป็นคนเลือกทั้งหมด\n\n")
        return self._class_step(data, channel_id, intro=intro)

    async def handle(self, draft: CharacterDraft, data: dict, text: str,
                     channel_id: str) -> BridgeResult:
        build = data.get("_build") or {}
        step = build.get("step", "class")
        handler = getattr(self, f"_on_{step}", None)
        if handler is None:  # unknown state — restart the build safely
            return await self.start(draft, data, channel_id)
        return await handler(draft, data, text.strip(), channel_id)

    # ---------- step: class -----------------------------------------------------
    def _rank_classes(self, data: dict) -> list[str]:
        blob = " ".join(str(data.get(k, "")) for k in ("concept", "origin", "desire", "flaw"))
        scored = []
        for name, cls in self.reg.classes.items():
            hits = sum(1 for kw in cls.concept_keywords if kw in blob)
            scored.append((-hits, name))
        scored.sort()
        return [name for _, name in scored]

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
            return self._class_step(data, channel_id, show_all=True)
        picked = _match(text, self.reg.classes)
        if picked is None:
            return self._class_step(data, channel_id,
                                    intro="เลือกจากปุ่ม หรือพิมพ์ชื่อ Class ได้เลย\n\n")
        data["_build"].update({"step": "species", "class": picked})
        await self._save(draft, data)
        return self._species_step(data, channel_id)

    # ---------- step: species ----------------------------------------------------
    def _species_step(self, data: dict, channel_id: str) -> BridgeResult:
        blob = " ".join(str(v) for v in data.values() if isinstance(v, str))
        hinted = next((n for n, th in (("elf", "เอลฟ์"), ("dwarf", "แคระ"),
                                       ("halfling", "ฮาล์ฟลิง")) if th in blob), "human")
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

    async def _on_abilities(self, draft, data, text, channel_id) -> BridgeResult:
        if USE_RECOMMENDED in text:
            scores = self._recommended_scores(data)
        elif ARRANGE_MYSELF in text:
            return _card(channel_id, "จัดค่าเอง",
                         "พิมพ์การจัดของเจ้า เช่น `STR 8 DEX 12 CON 13 INT 15 WIS 14 CHA 10`\n"
                         f"ต้องใช้ตัวเลขชุดนี้ครบทุกตัว: {', '.join(map(str, STANDARD_ARRAY))}", [])
        else:
            scores = _parse_scores(text)
            if scores is None:
                return _card(channel_id, "ยังอ่านไม่ออก",
                             "รูปแบบ: `STR 8 DEX 12 CON 13 INT 15 WIS 14 CHA 10` "
                             f"และต้องเป็นชุด {STANDARD_ARRAY} พอดี", [USE_RECOMMENDED])
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
        return _card(channel_id, "เลือกทักษะถนัด", body,
                     [f"{self.reg.skills[s].name_th} ({s})" for s in options])

    async def _on_skills(self, draft, data, text, channel_id) -> BridgeResult:
        options, count = self._skill_options(data)
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
        return _card(channel_id, f"{trait.name_th} — เลือกทักษะเพิ่ม",
                     trait.summary_th,
                     [f"{self.reg.skills[s].name_th} ({s})" for s in opts])

    async def _on_species_skill(self, draft, data, text, channel_id) -> BridgeResult:
        trait_key = data["_build"].get("species_skill_trait", "")
        picked = _match(text, self.reg.skills)
        if picked is None:
            sp = self.reg.get_species(data["_build"]["species"])
            trait = next(t for t in sp.traits if t.key == trait_key)
            return self._species_skill_step(data, channel_id, trait)
        data["_build"][f"species_skill:{trait_key}"] = picked
        await self._save(draft, data)
        return await self._after_skills(draft, data, channel_id)

    def _expertise_step(self, data, channel_id) -> BridgeResult:
        chosen = data["_build"].get("expertise", [])
        opts = [s for s in self._all_skills_so_far(data) if s not in chosen]
        return _card(channel_id, "ความเชี่ยวชาญ (Expertise)",
                     f"เลือกทักษะที่ถนัดเป็นพิเศษ — โบนัสความถนัดคูณสอง\n"
                     f"**เลือกแล้ว {len(chosen)} / 2**",
                     [f"{self.reg.skills[s].name_th} ({s})" for s in opts])

    async def _on_expertise(self, draft, data, text, channel_id) -> BridgeResult:
        opts = self._all_skills_so_far(data)
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
            data["_build"].update({"step": "cantrips", "cantrips": []})
            await self._save(draft, data)
            return self._spell_pick_step(data, channel_id, level=0,
                                         key="cantrips", count=sc.cantrips_known,
                                         title="เลือกคาถาประจำตัว (Cantrips)")
        if sc and sc.spellbook_size > 0 and "book" not in data["_build"]:
            data["_build"].update({"step": "book", "book": []})
            await self._save(draft, data)
            return self._spell_pick_step(data, channel_id, level=1,
                                         key="book", count=sc.spellbook_size,
                                         title="คัดคาถาลงตำรา (Spellbook)")
        if sc and sc.prepared_count > 0 and "prepared" not in data["_build"]:
            data["_build"].update({"step": "prepared", "prepared": []})
            await self._save(draft, data)
            return self._prepared_step(data, channel_id)
        data["_build"]["step"] = "review"
        await self._save(draft, data)
        return self._review_step(data, channel_id)

    def _spell_pick_step(self, data, channel_id, *, level, key, count, title) -> BridgeResult:
        cls = self.reg.get_class(data["_build"]["class"])
        chosen = data["_build"].get(key, [])
        pool = [s for s in self.reg.spells_for_class(cls.spellcasting.spell_list, level)
                if s.name not in chosen]
        by_cat: dict[str, list] = {}
        for s in pool:
            by_cat.setdefault(s.ux_category, []).append(s)
        lines = []
        for cat, spells in by_cat.items():
            lines.append(f"__{cat}__")
            for s in spells:
                conc = " · ต้องเพ่งสมาธิ" if s.concentration else ""
                lines.append(f"**{s.name_th_hint}** ({s.name}) — {s.mech_summary_th}{conc}")
        body = f"**เลือกแล้ว {len(chosen)} / {count}**\n\n" + "\n".join(lines)
        return _card(channel_id, title, body,
                     [f"{s.name_th_hint} ({s.name})" for s in pool])

    async def _on_cantrips(self, draft, data, text, channel_id) -> BridgeResult:
        return await self._on_spell_pick(draft, data, text, channel_id, key="cantrips",
                                         level=0)

    async def _on_book(self, draft, data, text, channel_id) -> BridgeResult:
        return await self._on_spell_pick(draft, data, text, channel_id, key="book",
                                         level=1)

    async def _on_spell_pick(self, draft, data, text, channel_id, *, key, level) -> BridgeResult:
        cls = self.reg.get_class(data["_build"]["class"])
        sc = cls.spellcasting
        count = sc.cantrips_known if key == "cantrips" else sc.spellbook_size
        pool = {s.name: s for s in self.reg.spells_for_class(sc.spell_list, level)}
        picked = _match(text, pool)
        if picked and picked not in data["_build"][key]:
            data["_build"][key].append(picked)
        if len(data["_build"][key]) < count:
            await self._save(draft, data)
            title = "เลือกคาถาประจำตัว (Cantrips)" if key == "cantrips" else "คัดคาถาลงตำรา (Spellbook)"
            return self._spell_pick_step(data, channel_id, level=level, key=key,
                                         count=count, title=title)
        return await self._to_spells_or_review(draft, data, channel_id)

    def _prepared_step(self, data, channel_id) -> BridgeResult:
        cls = self.reg.get_class(data["_build"]["class"])
        sc = cls.spellcasting
        chosen = data["_build"].get("prepared", [])
        source = (data["_build"].get("book")
                  or [s.name for s in self.reg.spells_for_class(sc.spell_list, 1)])
        pool = [s for s in source if s not in chosen]
        lines = [f"**{self.reg.get_spell(s).name_th_hint}** — {self.reg.get_spell(s).mech_summary_th}"
                 for s in pool]
        body = (f"คาถาที่ 'เตรียมไว้' คือชุดที่พร้อมร่ายในแต่ละวัน (เปลี่ยนได้หลังพักยาว)\n"
                f"**เลือกแล้ว {len(chosen)} / {sc.prepared_count}**\n\n" + "\n".join(lines))
        return _card(channel_id, "เตรียมคาถา (Prepared)", body,
                     [f"{self.reg.get_spell(s).name_th_hint} ({s})" for s in pool])

    async def _on_prepared(self, draft, data, text, channel_id) -> BridgeResult:
        cls = self.reg.get_class(data["_build"]["class"])
        sc = cls.spellcasting
        source = (data["_build"].get("book")
                  or [s.name for s in self.reg.spells_for_class(sc.spell_list, 1)])
        picked = _match(text, {s: None for s in source})
        if picked and picked not in data["_build"]["prepared"]:
            data["_build"]["prepared"].append(picked)
        if len(data["_build"]["prepared"]) < sc.prepared_count:
            await self._save(draft, data)
            return self._prepared_step(data, channel_id)
        data["_build"]["step"] = "review"
        await self._save(draft, data)
        return self._review_step(data, channel_id)

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
        lines = [
            f"**{data.get('name', 'นักผจญภัย')}** — {sp.name_th} · {cls.name_th} · {bg.name_th}",
            score_line,
            f"ทักษะถนัด: {', '.join(self.reg.skills[s].name_th for s in skills)}",
        ]
        if b.get("expertise"):
            lines.append(f"เชี่ยวชาญพิเศษ: {', '.join(self.reg.skills[s].name_th for s in b['expertise'])}")
        if b.get("cantrips"):
            lines.append(f"Cantrips: {', '.join(self.reg.get_spell(s).name_th_hint for s in b['cantrips'])}")
        if b.get("book"):
            lines.append(f"ตำราคาถา: {', '.join(self.reg.get_spell(s).name_th_hint for s in b['book'])}")
        if b.get("prepared"):
            lines.append(f"เตรียมไว้: {', '.join(self.reg.get_spell(s).name_th_hint for s in b['prepared'])}")
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
        async with self.db.unit_of_work() as s:
            row = await s.get(CharacterDraft, draft.id)
            row.data = data


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
