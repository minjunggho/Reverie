"""RestService — Short/Long Rests as real domain operations (SRD 5.2.1; §13).

A rest advances the world clock through its window. If a PERCEIVABLE scheduled
world event fires inside the window, the rest is INTERRUPTED: per the 2024 rule an
interrupted rest confers no benefits and must be restarted — the clock advance and
any threat ticks stand (the world does not rewind), only the benefits are withheld.

Completion applies, per SRD 5.2.1:
- Short Rest (60 min): spend Hit Dice (roll die + CON mod each, engine dice),
  partial/short-rest resource recharges.
- Long Rest (480 min): HP to max, regain half max Hit Dice (min 1), all resource
  pools (spell slots included), Exhaustion −1; dying/stable states reset for the
  living. Spell re-preparation opens (handled by the spells view/flow).

Operates via the caller-owned db (unit-of-work per step), pairing every mutation
with canonical events.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import ValidationError
from app.core.ids import entity_ref
from app.core.randomness import Randomness
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.services.events import EventService
from app.tabletop.dice import DiceEngine
from app.tabletop.resources import ResourceEngine
from app.tabletop.rules.core import ability_modifier
from app.world.world_clock import WorldClockService

SHORT_REST_MINUTES = 60
LONG_REST_MINUTES = 480


@dataclass
class RestOutcome:
    kind: str                      # "short" | "long"
    completed: bool
    interrupted_by: list[str] = field(default_factory=list)   # perceivable notes
    minutes_elapsed: int = 0
    notes_th: dict[str, list[str]] = field(default_factory=dict)  # char name -> notes


class RestService:
    def __init__(self, db, rng: Randomness) -> None:
        self.db = db
        self.dice = DiceEngine(rng)

    async def short_rest(
        self, *, campaign_id: str, character_ids: list[str],
        session_id: str | None = None,
        spend_hit_dice: dict[str, int] | None = None,   # char_id -> dice to spend
    ) -> RestOutcome:
        return await self._rest(
            campaign_id=campaign_id, character_ids=character_ids, session_id=session_id,
            kind="short", minutes=SHORT_REST_MINUTES, spend_hit_dice=spend_hit_dice or {},
        )

    async def long_rest(
        self, *, campaign_id: str, character_ids: list[str],
        session_id: str | None = None,
    ) -> RestOutcome:
        return await self._rest(
            campaign_id=campaign_id, character_ids=character_ids, session_id=session_id,
            kind="long", minutes=LONG_REST_MINUTES, spend_hit_dice={},
        )

    async def _rest(self, *, campaign_id, character_ids, session_id, kind, minutes,
                    spend_hit_dice) -> RestOutcome:
        if not character_ids:
            raise ValidationError("rest requires at least one character")

        # 1. The world keeps moving: advance the clock through the window.
        async with self.db.unit_of_work() as s:
            clock = await WorldClockService(s).advance_time(
                campaign_id=campaign_id, minutes=minutes, session_id=session_id,
                actor_entity="system",
            )
            interrupted = list(clock.perceivable_notes)

        if interrupted:
            # 2a. Interrupted: no benefits (2024 restart rule); record the fact.
            async with self.db.unit_of_work() as s:
                await EventService(s).record(
                    campaign_id=campaign_id, session_id=session_id,
                    event_type=EventType.WORLD_TIME_ADVANCED, actor_entity="system",
                    visibility=Visibility.PARTY,
                    payload={"summary": f"การพัก{'สั้น' if kind == 'short' else 'ยาว'}ถูกขัดจังหวะ",
                             "rest": kind, "interrupted": True},
                    narrative_significance=25,
                )
            return RestOutcome(kind=kind, completed=False,
                               interrupted_by=interrupted, minutes_elapsed=minutes)

        # 2b. Completed: apply benefits per character, atomically with events.
        outcome = RestOutcome(kind=kind, completed=True, minutes_elapsed=minutes)
        async with self.db.unit_of_work() as s:
            engine = ResourceEngine(s)
            events = EventService(s)
            for cid in character_ids:
                char = await s.get(Character, cid)
                if char is None or char.dead:
                    continue
                notes: list[str] = []
                if kind == "short":
                    notes += await self._apply_short(s, engine, char,
                                                     spend_hit_dice.get(cid, 0))
                else:
                    notes += await self._apply_long(engine, char)
                    notes += await engine.apply_long_rest(cid)
                outcome.notes_th[char.name] = notes
                await events.record(
                    campaign_id=campaign_id, session_id=session_id,
                    event_type=EventType.WORLD_TIME_ADVANCED,
                    actor_entity=entity_ref("character", cid),
                    visibility=Visibility.PARTY,
                    payload={"summary": f"{char.name} พัก{'สั้น' if kind == 'short' else 'ยาว'}เสร็จ",
                             "rest": kind, "notes": notes},
                    mechanical_changes={"hp": {"to": char.hp},
                                        "hit_dice_remaining": char.hit_dice_remaining},
                    narrative_significance=15,
                )
        return outcome

    async def _apply_short(self, s, engine: ResourceEngine, char: Character,
                           dice_to_spend: int) -> list[str]:
        notes: list[str] = []
        spend = max(0, min(dice_to_spend, char.hit_dice_remaining))
        healed_total = 0
        for _ in range(spend):
            if char.hp >= char.max_hp:
                break
            roll = self.dice.roll_die(char.hit_die)
            healed = max(0, roll + ability_modifier(char.con_score))
            char.hp = min(char.max_hp, char.hp + healed)
            char.hit_dice_remaining -= 1
            healed_total += healed
        if healed_total:
            notes.append(f"ใช้ Hit Dice ฟื้น {healed_total} HP (เหลือ {char.hit_dice_remaining} ลูก)")
        notes += await engine.apply_short_rest(char.id)
        return notes

    async def _apply_long(self, engine: ResourceEngine, char: Character) -> list[str]:
        notes: list[str] = []
        if char.hp < char.max_hp:
            notes.append(f"HP เต็ม ({char.max_hp})")
        char.hp = char.max_hp
        char.temp_hp = 0
        regain = max(1, char.level // 2)
        before = char.hit_dice_remaining
        char.hit_dice_remaining = min(char.level, char.hit_dice_remaining + regain)
        if char.hit_dice_remaining != before:
            notes.append(f"Hit Dice คืนมา {char.hit_dice_remaining - before} ลูก")
        if char.exhaustion > 0:
            char.exhaustion -= 1
            notes.append(f"ความอ่อนล้าลดลง (เหลือระดับ {char.exhaustion})")
        char.stable = False
        char.death_saves = {"successes": 0, "failures": 0}
        return notes
