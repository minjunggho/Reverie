"""Canonical enumerations shared across models, schemas, and services.

Stored as their string `.value` in the DB (portable, human-readable). See
`docs/domain-model.md` for the authoritative summary.
"""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class CampaignStatus(StrEnum):
    SETUP = "SETUP"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class SessionStatus(StrEnum):
    PREPARATION = "PREPARATION"
    OPENING = "OPENING"
    ACTIVE_PLAY = "ACTIVE_PLAY"
    CLOSING = "CLOSING"
    POST_SESSION = "POST_SESSION"
    COMPLETE = "COMPLETE"


class ActivePlayState(StrEnum):
    SCENE_FRAMING = "SCENE_FRAMING"
    TABLE_OPEN = "TABLE_OPEN"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    ADJUDICATING = "ADJUDICATING"
    RESOLVING = "RESOLVING"
    COMMITTING_STATE = "COMMITTING_STATE"
    NARRATING = "NARRATING"
    SCENE_TRANSITION = "SCENE_TRANSITION"
    COMBAT_INITIALIZING = "COMBAT_INITIALIZING"
    COMBAT_ACTIVE = "COMBAT_ACTIVE"
    COMBAT_RESOLVING_TURN = "COMBAT_RESOLVING_TURN"


class MemberRole(StrEnum):
    OWNER = "OWNER"
    PLAYER = "PLAYER"


class SceneMode(StrEnum):
    EXPLORATION = "EXPLORATION"
    SOCIAL = "SOCIAL"
    DOWNTIME = "DOWNTIME"
    COMBAT = "COMBAT"


class SceneStatus(StrEnum):
    ACTIVE = "ACTIVE"
    TRANSITIONING = "TRANSITIONING"
    CLOSED = "CLOSED"


class WindowPhase(StrEnum):
    """Explicit state machine for a shared decision window (one round). Behaviour is
    driven by this phase, never by chat-message arrival order."""
    AWAITING_ACTIONS = "AWAITING_ACTIONS"      # players submit/edit/ready
    VALIDATING = "VALIDATING"                  # deterministic checks before locking
    AWAITING_ROLLS = "AWAITING_ROLLS"          # manual-dice: required rolls outstanding
    READY_TO_RESOLVE = "READY_TO_RESOLVE"      # frozen snapshot; rolls in hand
    RESOLVING = "RESOLVING"                    # engine applying the frozen set
    PRESENTING_RESULTS = "PRESENTING_RESULTS"  # one combined narration produced
    ROUND_COMPLETE = "ROUND_COMPLETE"          # terminal; next window may open
    CANCELLED = "CANCELLED"                    # host cancelled the round


class WindowMode(StrEnum):
    COMBAT = "COMBAT"          # initiative-ordered resolution
    NONCOMBAT = "NONCOMBAT"    # relationship-classified resolution


class SubmissionValidation(StrEnum):
    PENDING = "PENDING"                  # not yet validated this revision
    VALID = "VALID"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"


class SubmissionVisibility(StrEnum):
    OPEN = "OPEN"        # other players see it in the planning panel
    SECRET = "SECRET"    # hidden from other players until resolution


class ActionRelationship(StrEnum):
    """How one submitted action relates to another in the same window. Decided by the
    resolver BEFORE narration, so the combined scene reflects real interaction."""
    COOPERATIVE = "COOPERATIVE"          # lift the door / crawl under
    INDEPENDENT = "INDEPENDENT"          # search desk / question prisoner
    CONFLICTING = "CONFLICTING"          # free the prisoner / execute them
    SEQUENTIAL = "SEQUENTIAL"            # distract guard so ally steals key
    MUTUALLY_EXCLUSIVE = "MUTUALLY_EXCLUSIVE"  # both claim the same restricted space
    SECRET = "SECRET"                    # hidden from the others
    INTERRUPTING = "INTERRUPTING"        # a reaction/interrupt of another action
    SOCIAL_OVERLAP = "SOCIAL_OVERLAP"    # talking over each other to the same NPC


class MessageCategory(StrEnum):
    COMMITTED_ACTION = "COMMITTED_ACTION"
    DM_QUESTION = "DM_QUESTION"
    RULES_QUESTION = "RULES_QUESTION"
    CHARACTER_DIALOGUE = "CHARACTER_DIALOGUE"
    OOC_DISCUSSION = "OOC_DISCUSSION"
    SOCIAL_OR_JOKE = "SOCIAL_OR_JOKE"
    UNKNOWN = "UNKNOWN"


class CommitmentSource(StrEnum):
    EXPLICIT_PREFIX = "EXPLICIT_PREFIX"  # implemented
    # Reserved for the future — NOT implemented in the MVP:
    AI_INFERRED = "AI_INFERRED"
    DISCORD_BUTTON = "DISCORD_BUTTON"
    VOICE_CONFIRMED = "VOICE_CONFIRMED"


class Visibility(StrEnum):
    PUBLIC = "PUBLIC"
    PARTY = "PARTY"
    PLAYER_ONLY = "PLAYER_ONLY"
    DM_ONLY = "DM_ONLY"
    NPC_SCOPED = "NPC_SCOPED"


class KnowledgeStatus(StrEnum):
    KNOWS = "KNOWS"
    BELIEVES = "BELIEVES"
    SUSPECTS = "SUSPECTS"
    HEARD_RUMOR = "HEARD_RUMOR"
    FORGOTTEN = "FORGOTTEN"
    UNAWARE = "UNAWARE"


class ProcessingStage(StrEnum):
    RECEIVED = "RECEIVED"
    INTERPRETED = "INTERPRETED"
    ADJUDICATED = "ADJUDICATED"
    RESOLVED = "RESOLVED"
    COMMITTED = "COMMITTED"
    NARRATED = "NARRATED"
    SENT = "SENT"
    FAILED = "FAILED"


class ResolutionType(StrEnum):
    AUTOMATIC_SUCCESS = "AUTOMATIC_SUCCESS"
    AUTOMATIC_FAILURE = "AUTOMATIC_FAILURE"
    ABILITY_CHECK = "ABILITY_CHECK"
    SAVING_THROW = "SAVING_THROW"
    ATTACK = "ATTACK"
    SUPPORTED_SPECIAL_RESOLUTION = "SUPPORTED_SPECIAL_RESOLUTION"


class ConsequenceClass(StrEnum):
    SUCCESS = "SUCCESS"
    SUCCESS_WITH_COST = "SUCCESS_WITH_COST"
    FAILURE = "FAILURE"
    FAILURE_WITH_CONSEQUENCE = "FAILURE_WITH_CONSEQUENCE"
    FAILURE_WITH_PROGRESS = "FAILURE_WITH_PROGRESS"


class EventType(StrEnum):
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_ENDED = "SESSION_ENDED"
    SCENE_STARTED = "SCENE_STARTED"
    PLAYER_ACTION_COMMITTED = "PLAYER_ACTION_COMMITTED"
    ABILITY_CHECK_RESOLVED = "ABILITY_CHECK_RESOLVED"
    ATTACK_RESOLVED = "ATTACK_RESOLVED"
    DAMAGE_APPLIED = "DAMAGE_APPLIED"
    SPELL_CAST = "SPELL_CAST"
    RESOURCE_SPENT = "RESOURCE_SPENT"
    FEATURE_USED = "FEATURE_USED"
    ITEM_GAINED = "ITEM_GAINED"
    ITEM_LOST = "ITEM_LOST"
    ITEM_TRANSFERRED = "ITEM_TRANSFERRED"
    CHARACTER_MOVED = "CHARACTER_MOVED"
    NPC_STATE_CHANGED = "NPC_STATE_CHANGED"
    KNOWLEDGE_GAINED = "KNOWLEDGE_GAINED"
    QUEST_STATE_CHANGED = "QUEST_STATE_CHANGED"
    WORLD_TIME_ADVANCED = "WORLD_TIME_ADVANCED"
    THREAT_ADVANCED = "THREAT_ADVANCED"
    COMBAT_STARTED = "COMBAT_STARTED"
    COMBAT_ENDED = "COMBAT_ENDED"
    # Persistent-world consequences (§11–13).
    NPC_INJURED = "NPC_INJURED"
    CRIME_RECORDED = "CRIME_RECORDED"
    CRIME_DISCOVERED = "CRIME_DISCOVERED"
    REPUTATION_CHANGED = "REPUTATION_CHANGED"
    FACTION_ADVANCED = "FACTION_ADVANCED"
    ACCESS_STATE_CHANGED = "ACCESS_STATE_CHANGED"
    LOCATION_STATE_CHANGED = "LOCATION_STATE_CHANGED"
    RUMOR_SPREAD = "RUMOR_SPREAD"
    ROUTE_DISCOVERED = "ROUTE_DISCOVERED"
    CONSEQUENCE_SCHEDULED = "CONSEQUENCE_SCHEDULED"


class AssistanceLevel(StrEnum):
    MINIMAL = "MINIMAL"
    BEGINNER = "BEGINNER"


class DifficultyBand(StrEnum):
    VERY_EASY = "VERY_EASY"      # 5
    EASY = "EASY"                # 10
    MEDIUM = "MEDIUM"            # 15
    HARD = "HARD"               # 20
    VERY_HARD = "VERY_HARD"     # 25
    NEARLY_IMPOSSIBLE = "NEARLY_IMPOSSIBLE"  # 30


BAND_TO_DC: dict[DifficultyBand, int] = {
    DifficultyBand.VERY_EASY: 5,
    DifficultyBand.EASY: 10,
    DifficultyBand.MEDIUM: 15,
    DifficultyBand.HARD: 20,
    DifficultyBand.VERY_HARD: 25,
    DifficultyBand.NEARLY_IMPOSSIBLE: 30,
}
