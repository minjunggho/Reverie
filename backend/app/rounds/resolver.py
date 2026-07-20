"""RoundResolver — turn one frozen set of intentions into one coherent world update.

Collecting actions together does NOT mean pretending they happen at the same mechanical
instant: combat resolves in verified initiative order, and an earlier action that kills a
target invalidates a later action against it — which then uses a declared fallback, or is
safely skipped per policy (never silently replaced with a different major action). The
resolver emits a structured `RoundPackage` (persisted for replay) and asks the narrator
for ONE connected scene built from the players' own words.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select

from app.ai.llm.base import LLMProvider
from app.ai.prompts.system_prompts import ROUND_NARRATOR_SYSTEM
from app.ai.prompts.thai_dm_style import THAI_DM_STYLE
from app.ai.prompts.thai_narration_templates import narration_template
from app.core.errors import RulesViolation
from app.core.logging import get_logger
from app.entities import SceneEntityDirectory
from app.models.combat import Combatant, CombatEncounter
from app.models.decision_window import DecisionWindow
from app.models.enums import WindowMode, WindowPhase
from app.memory.scene_packet import ScenePacketBuilder
from app.rounds.classifier import classify_relationships
from app.rounds.policies import WindowPolicies
from app.rounds.service import DecisionWindowService
from app.services.scenes import SceneService
from app.tabletop.combat import CombatService
from app.tabletop.dice import DiceEngine

log = get_logger(__name__)


@dataclass
class ResolvedAction:
    actor_id: str
    order_index: int
    relationship: str
    status: str          # resolved | fallback | invalidated | passed | skipped | needs_decision
    used_fallback: bool = False
    note: str = ""
    outcome: dict = field(default_factory=dict)


@dataclass
class RoundPackage:
    """The structured hand-off to narration (and the replayable record of the round)."""
    window_id: str
    round_id: int
    mode: str
    initiative: list[str] = field(default_factory=list)
    intentions: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    per_actor_relationship: dict = field(default_factory=dict)
    resolved_actions: list[dict] = field(default_factory=list)
    roll_results: list[dict] = field(default_factory=list)
    invalidated: list[dict] = field(default_factory=list)
    fallbacks: list[dict] = field(default_factory=list)
    damage: list[dict] = field(default_factory=list)
    conditions: list[dict] = field(default_factory=list)
    final_positions: dict = field(default_factory=dict)
    environment_changes: list = field(default_factory=list)
    objective_changes: list = field(default_factory=list)
    npc_intentions: list = field(default_factory=list)
    scene_packet: dict = field(default_factory=dict)
    narration: str = ""
    decision_prompt: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class RoundResolver:
    def __init__(self, db, provider: LLMProvider | None, rng) -> None:
        self.db = db
        self.provider = provider
        self.rng = rng

    async def resolve(self, *, window_id: str) -> RoundPackage:
        # Duplicate-resolve guard: a resolved round returns its stored package rather
        # than applying the world update twice.
        async with self.db.session() as read:
            window = await read.get(DecisionWindow, window_id)
            if window is not None and window.resolved and window.round_package:
                return RoundPackage(**window.round_package)

        async with self.db.unit_of_work() as s:
            svc = DecisionWindowService(s)
            window = await svc.get(window_id)
            if window.resolved:
                return RoundPackage(**(window.round_package or {"window_id": window_id,
                                                                "round_id": window.round_id,
                                                                "mode": window.mode}))
            snapshot = await svc.freeze(window)     # idempotent freeze
            window.phase = WindowPhase.RESOLVING.value

            subs = [x for x in snapshot["submissions"] if not x.get("passed")]
            scene = await SceneService(s).get_active_scene(window.session_id)
            directory = await SceneEntityDirectory(s).build(
                scene, actor_character_id=None, campaign_id=window.campaign_id)

            pkg = RoundPackage(window_id=window.id, round_id=window.round_id, mode=window.mode)
            pkg.intentions = snapshot["submissions"]

            if window.mode == WindowMode.COMBAT.value:
                await self._resolve_combat(s, window, subs, directory, pkg)
            else:
                await self._resolve_noncombat(subs, directory, pkg)

            # Narration receives the same bounded canonical packet as session start,
            # now projected AFTER verified mechanics so injuries/effects/objects and
            # location changes carry into this combined scene.
            if scene is not None:
                packet = await ScenePacketBuilder(s).build(
                    campaign_id=window.campaign_id,
                    session_id=window.session_id,
                    scene=scene,
                    narration_mode="shared_action_resolution",
                    decision_window=window,
                )
                pkg.scene_packet = asdict(packet)

            window.round_package = pkg.as_dict()
            window.resolved = True
            window.phase = WindowPhase.PRESENTING_RESULTS.value
            window.version += 1

        # One combined narration, generated after the world update is committed (so a
        # narration failure can never re-apply the round). Best-effort.
        narration, prompt = await self._narrate(window_id, pkg)
        pkg.narration, pkg.decision_prompt = narration, prompt
        async with self.db.unit_of_work() as s:
            window = await s.get(DecisionWindow, window_id)
            if window is not None:
                stored = dict(window.round_package or {})
                stored["narration"] = narration
                stored["decision_prompt"] = prompt
                window.round_package = stored
                window.phase = WindowPhase.ROUND_COMPLETE.value
        return pkg

    # --- combat: verified initiative order, kill-invalidates-later -----------
    async def _resolve_combat(self, s, window, subs, directory, pkg: RoundPackage) -> None:
        enc = (await s.execute(select(CombatEncounter).where(
            CombatEncounter.session_id == window.session_id,
            CombatEncounter.status == "active"))).scalars().first()
        if enc is None:
            # No encounter — fall back to intention-only records rather than inventing.
            await self._resolve_noncombat(subs, directory, pkg)
            return
        combat = CombatService(s, DiceEngine(self.rng))
        by_ref: dict[str, Combatant] = {
            c.entity_ref: c for c in (await s.execute(select(Combatant).where(
                Combatant.encounter_id == enc.id))).scalars()}

        def _combatant_for(ref_text: str) -> Combatant | None:
            if not ref_text:
                return None
            res = directory.resolve_mentions([ref_text])
            for e in res.resolved:
                if e.entity_ref in by_ref:
                    return by_ref[e.entity_ref]
            return None

        # Verified initiative order over the SUBMITTING actors (highest first).
        def _init(sub) -> int:
            cb = by_ref.get(f"character:{sub['actor_id']}")
            return cb.initiative if cb else -1
        ordered = sorted(subs, key=lambda x: (-_init(x), x["actor_id"]))
        pkg.initiative = [f"character:{x['actor_id']}" for x in ordered]
        rmap = classify_relationships(subs)
        pkg.per_actor_relationship = rmap.per_actor
        pkg.relationships = rmap.pairs

        for idx, sub in enumerate(ordered):
            actor = sub["actor_id"]
            rel = rmap.per_actor.get(actor, "INDEPENDENT")
            attacker = by_ref.get(f"character:{actor}")
            target = _combatant_for(sub.get("action_target"))
            if attacker is None:
                pkg.resolved_actions.append(asdict(ResolvedAction(
                    actor, idx, rel, "skipped", note="ไม่มีสถานะในการต่อสู้")))
                continue
            # An earlier kill this round invalidates a later action against that target.
            if target is None or not target.alive:
                fb = _combatant_for(sub.get("fallback_target"))
                if sub.get("fallback_target") and fb is not None and fb.alive:
                    out = await combat._resolve_attack(enc, attacker, fb.id, interrupt=False)
                    pkg.fallbacks.append({"actor_id": actor, "to": fb.entity_ref})
                    self._record_attack(pkg, actor, idx, rel, out, status="fallback",
                                        used_fallback=True,
                                        note="เป้าหมายเดิมล้มไปแล้ว ใช้เป้าหมายสำรอง")
                else:
                    pkg.invalidated.append({"actor_id": actor,
                                            "reason": "target_down_or_absent"})
                    pkg.resolved_actions.append(asdict(ResolvedAction(
                        actor, idx, rel, "invalidated",
                        note="เป้าหมายล้ม/ไม่อยู่แล้ว และไม่มีแผนสำรอง")))
                continue
            out = await combat._resolve_attack(enc, attacker, target.id, interrupt=False)
            self._record_attack(pkg, actor, idx, rel, out, status="resolved")

        pkg.final_positions = {c.entity_ref: {"hp": c.hp, "alive": c.alive}
                               for c in by_ref.values()}

    def _record_attack(self, pkg, actor, idx, rel, out, *, status, used_fallback=False, note=""):
        pkg.roll_results.append({
            "actor_id": actor, "natural_roll": out.natural_roll,
            "attack_total": out.attack_total, "target_ac": out.target_ac, "hit": out.hit})
        if out.damage:
            pkg.damage.append({"target": out.target, "amount": out.damage,
                               "hp_from": out.hp_before, "hp_to": out.hp_after})
        if out.target_down:
            pkg.conditions.append({"target": out.target, "condition": "down"})
        pkg.resolved_actions.append(asdict(ResolvedAction(
            actor, idx, rel, status, used_fallback=used_fallback, note=note,
            outcome={"hit": out.hit, "damage": out.damage, "target": out.target,
                     "target_down": out.target_down})))

    # --- noncombat: relationship-classified, ordered by dependency -----------
    async def _resolve_noncombat(self, subs, directory, pkg: RoundPackage) -> None:
        rmap = classify_relationships(subs)
        pkg.per_actor_relationship = rmap.per_actor
        pkg.relationships = rmap.pairs
        deps = rmap.dependencies()
        # Dependency-first ordering: an action others depend on resolves before them.
        ordered = sorted(subs, key=lambda x: (x["actor_id"] in deps, x["actor_id"]))
        pkg.initiative = [f"character:{x['actor_id']}" for x in ordered]
        for idx, sub in enumerate(ordered):
            actor = sub["actor_id"]
            rel = rmap.per_actor.get(actor, "INDEPENDENT")
            # The generic non-combat resolver has not run the adjudicator/dice path.
            # Record the coordinated INTENT, never mislabel it as a verified success.
            # The narrator may stage the attempts but must stop before any uncertain
            # outcome.  Free/automatic domain actions are integrated separately by
            # the committed pipeline as that coverage grows.
            status = "intent_recorded"
            note = ""
            tgt = sub.get("action_target")
            if tgt:
                res = directory.resolve_mentions([tgt])
                if res.not_present and not res.resolved:
                    if sub.get("fallback_action"):
                        status, note = "fallback", "เป้าหมายเดิมไม่อยู่ ใช้แผนสำรอง"
                        pkg.fallbacks.append({"actor_id": actor, "to": sub.get("fallback_action")})
                    else:
                        status, note = "invalidated", "เป้าหมายไม่อยู่ในฉาก"
                        pkg.invalidated.append({"actor_id": actor, "reason": "target_absent"})
            pkg.resolved_actions.append(asdict(ResolvedAction(
                actor, idx, rel, status, used_fallback=(status == "fallback"), note=note,
                outcome={"intent": sub.get("primary_action") or sub.get("raw_player_text")})))

    # --- one combined scene from the players' own words ----------------------
    async def _narrate(self, window_id: str, pkg: RoundPackage) -> tuple[str, str]:
        if self.provider is None:
            return "", ""
        try:
            messages = self._narration_messages(pkg)
            narration = await self.provider.generate_dm_narration(messages)
            from app.ai.narration_guard import screen_decision_prompt, screen_narration

            text, _ = screen_narration(narration.text)
            prompt = screen_decision_prompt(narration.decision_prompt) or ""
            return text, prompt
        except Exception as exc:  # noqa: BLE001 — narration is best-effort, post-commit
            log.warning("round narration failed; package stands without prose: %s", exc)
            return "", ""

    @staticmethod
    def _narration_messages(pkg: RoundPackage) -> list[dict]:
        import json

        lines = [f"ROUND_PACKAGE (mode={pkg.mode}, round={pkg.round_id})",
                 "ORDER (ตามลำดับกลไก): " + " > ".join(pkg.initiative)]
        if pkg.scene_packet:
            lines.append(
                "SCENE_PACKET (canonical continuity; missing fields must not be invented):\n"
                + json.dumps(pkg.scene_packet, ensure_ascii=False, sort_keys=True)
            )
        if pkg.relationships:
            lines.append("RELATIONSHIPS:")
            lines += [f"- {p['relationship']}: {p.get('note','')}" for p in pkg.relationships]
        lines.append("ACTIONS:")
        by_actor = {x["actor_id"]: x for x in pkg.intentions}
        for ra in pkg.resolved_actions:
            sub = by_actor.get(ra["actor_id"], {})
            raw = sub.get("raw_player_text") or sub.get("primary_action") or ""
            dlg = f" | DIALOGUE: “{sub['dialogue']}”" if sub.get("dialogue") else ""
            outcome = ra.get("outcome", {})
            verdict = ("โดน" if outcome.get("hit") else "พลาด") if "hit" in outcome else ra["status"]
            lines.append(f"- [{ra['status']}] {ra['actor_id']}: {raw}{dlg} -> {verdict}"
                         + (f" ({ra['note']})" if ra.get("note") else ""))
        if pkg.invalidated:
            lines.append("INVALIDATED: " + ", ".join(i["actor_id"] for i in pkg.invalidated))
        if pkg.fallbacks:
            lines.append("FALLBACKS: " + ", ".join(f["actor_id"] for f in pkg.fallbacks))
        return [
            {
                "role": "system",
                "content": (
                    THAI_DM_STYLE + "\n" + ROUND_NARRATOR_SYSTEM + "\n"
                    + narration_template("shared_action_resolution")
                ),
            },
            {"role": "user", "content": "\n".join(lines)},
        ]
