# Multiplayer Identity & Party Context

The domain distinctions Reverie must never collapse (P0 fix, 2026-07-10):

| Concept | Meaning | Where |
|---|---|---|
| **Discord user** | a real account (`minjunggho_`) | `User.discord_user_id` |
| **Campaign member** | that user's membership + role in ONE campaign | `CampaignMember` |
| **Player character** | the Character a member actively controls | `Character` (`owner_member_id`, and the member's `active_character_id`) |
| **Scene entity** | canonical in-world ref (`character:<id>`, `npc:<id>`) | scene ref lists |
| **Actor** | the Character declaring the current committed action | resolved by the bridge from the SENDER |
| **Target** | canonical entities referenced/affected by the action | resolved by `EntityResolver` |

Min controls Veskan; Friend controls Aria. Min ≠ Veskan; Friend ≠ Aria. Veskan and
Aria are both PLAYER_CHARACTERs — Aria is **never** a fresh NPC just because the
string "Aria" appears in Veskan's action.

## Presence is not participation is not spotlight is not turn order

- **Campaign member / party member / session attendee** — belongs to the group.
- **Physically present entity** — is in *this* scene (`scene.participants` for PCs;
  `scene.visible_entity_ids` / `immediate_threat_ids` for NPCs).
- **Spotlight** — recent narrative focus (`scene.spotlight`: `last_actor` +
  `action_counts`). Awareness only — it never forces turns.
- **Turn order** — initiative, and *only* inside combat.

Consequence: a party member who split off (not in `scene.participants`) is
**known-but-absent** — resolvable for a helpful "ไม่ได้อยู่ในฉากนี้" message, but not
a physically reachable target. `!rv party` membership must never imply presence.

## The one directory, task-specific views

`SceneEntityDirectory.build(scene, actor_character_id, campaign_id)` →
`SceneDirectory` of `EntityContext` (entity_ref, entity_type, canonical_name,
aliases, present_in_scene, player_controlled, controller_member_id, is_actor,
observable_state). Present PCs + NPCs feed interpreter/adjudicator/narrator; absent
party PCs are carried only for resolution fallback. Session opening, active play,
and `!rv party` all read this — no second definition of "the party".

## Target resolution (once, at the engine boundary)

```
! natural Thai action
  → ActionInterpreter extracts target_references (language mentions; never IDs)
  → SceneDirectory.resolve_mentions() → canonical refs (present) / not-present /
    ambiguous / unresolved
  → ambiguity → one focused clarification
  → not-present-only → "not reachable here"
  → resolved identities propagate to adjudication / consequence / narration
```

**Name matching** (`app/entities/directory.py`):
- Normalization = Unicode **NFC** + `str.casefold()` + collapsed whitespace. Thai has
  no case (casefold is a no-op); NFC folds composed/decomposed sequences.
- Precedence: (1) exact canonical name, (2) exact alias. **No substring/fuzzy** — so
  unrelated fantasy names never merge. Contextual/pronoun resolution is deferred.
- Multiple present candidates → ambiguous (clarify). The Discord display name is a
  separate namespace and is **never** auto-treated as a character alias.

**Aliases**: stored as `Character.aliases` (JSON list) — chosen over a normalized
`EntityAlias` table because the need today is a handful of explicit Thai
transliterations per character (e.g. `Aria` → `อาเรีย`), campaign-scoped implicitly
via the character. A normalized table becomes worth it once NPCs need many aliases or
cross-entity alias queries appear; revisit then.

## Player-character agency is inviolable

`ActionInterpretation.commands_other_pc` is true only when an action tries to dictate
**another** PC's *voluntary* choice ("Aria follows me", "Aria opens the door"). When
true and a target is a PLAYER_CHARACTER, the pipeline **refuses to execute** — it
frames the actor's request and hands the decision to that character's player. This is
enforced in the engine (`_agency_safe_response`), not merely asked of the narrator.

A physical action on a PC who *cannot* choose (dragging an unconscious ally out of
fire) is `commands_other_pc = false` → adjudicated normally against current state.

Consequence deltas remain typed-target-validated: `raise_suspicion` targets `npc:`
only (a PC can never receive NPC suspicion), and no delta can move/act another PC.

## Migration

Additive columns only: `characters.aliases` (JSON), `scenes.spotlight` (JSON). Tests
use `create_all`; the local dev SQLite must be deleted once (schema grew). Existing
Postgres campaigns get an Alembic autorevision. No data migration needed.
