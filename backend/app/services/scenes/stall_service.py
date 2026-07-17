"""StallService — the world notices when nothing is happening.

Two symptoms share one cause: "scenes can continue indefinitely after their purpose has
been exhausted" and "the world waits indefinitely while players repeat low-progress
actions". Nothing in the engine evaluated whether a turn accomplished anything, and
`app/scenes` — the module scene_service's docstring pointed at for exhaustion logic —
was never built (docs/progression-audit.md, RC5).

WHAT COUNTS AS PROGRESS is decided from committed state, never from the narrator's
opinion of the fiction. A turn made progress if it moved something the campaign tracks:
a clue opened something, an objective or chapter moved, the party travelled, or a
consequence delta actually changed the world. Talking is not failure — but three turns
of talking that change nothing IS the world's cue to move.

The response to a stall is pressure, never a correction. The engine does not tell
players they are doing it wrong; it lets time cost more (turn_clock) and surfaces the
fronts that are already moving. The party remains free to keep talking — the world just
stops politely waiting for them.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.scene import Scene

# Consecutive dead turns before the world leans in. Two is noise — a question and an
# answer legitimately change nothing. Three is a pattern.
STALL_THRESHOLD = 3


@dataclass(frozen=True)
class TurnProgress:
    """What a committed turn actually accomplished, from committed state only."""

    clue_opened: bool = False
    objective_moved: bool = False
    chapter_moved: bool = False
    travelled: bool = False
    world_changed: bool = False      # a consequence delta mutated something
    secret_revealed: bool = False

    @property
    def made_progress(self) -> bool:
        return any((self.clue_opened, self.objective_moved, self.chapter_moved,
                    self.travelled, self.world_changed, self.secret_revealed))


@dataclass(frozen=True)
class StallState:
    low_progress_turns: int
    stalled: bool

    def as_block(self) -> str:
        """A pacing signal for the narrator.

        It says only what is TRUE — the party has been circling — and never carries the
        fronts themselves: threats' next_action and progress are DM planning material,
        and narration produces player-facing prose. The world's actual push arrives
        through the clock (a stalled turn costs more minutes, so scheduled threat and
        faction beats fire and land as committed events the narrator is given
        normally). This block only stops the DM from writing another placid room.
        """
        if not self.stalled:
            return ""
        return (f"PACING: {self.low_progress_turns} ตาที่ผ่านมาไม่มีอะไรคืบหน้า "
                f"เวลาในโลกเดินอยู่ และโลกไม่ได้รอผู้เล่น — ให้ฉากมีความเคลื่อนไหวของมันเอง "
                f"(คนเดินผ่าน เสียงจากที่อื่น สิ่งที่เปลี่ยนไปเพราะเวลาผ่าน) "
                f"ห้ามบอกผู้เล่นว่าเขาทำผิด ห้ามสั่งให้เขาไปไหน และห้ามแต่งผลใหม่")


class StallService:
    """Owns `Scene.low_progress_turns`. Pure state, no LLM."""

    @staticmethod
    def record(scene: Scene | None, progress: TurnProgress) -> StallState:
        """Fold this turn's progress into the scene. Call once per committed action,
        inside the commit transaction, so a turn that never commits never counts."""
        if scene is None:
            return StallState(0, False)
        if progress.made_progress:
            scene.low_progress_turns = 0
        else:
            scene.low_progress_turns = (scene.low_progress_turns or 0) + 1
        return StallService.state(scene)

    @staticmethod
    def state(scene: Scene | None) -> StallState:
        count = (scene.low_progress_turns or 0) if scene is not None else 0
        return StallState(low_progress_turns=count, stalled=count >= STALL_THRESHOLD)
