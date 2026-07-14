# Faith system

## Phase 3 interaction architecture decision

Phase 3 adds a bounded `ReligiousInteractionService` over existing state rather
than adding another NPC-decision, memory, relationship, faction, or canon system.
The service reads Phase 1 deity relationships and Phase 2 `BeliefProfile` values;
NPC discoveries are stored as that NPC's existing `NPCFact` rows, meaningful
events as existing `NPCMemory` rows, and social effects as existing
`NPCRelationship` dimensions. Owner-approved temples and their mutable access or
reputation state use separately categorized `CampaignCanonRecord` documents.
Religious organizations reuse the current campaign faction representation
(`Threat`) by ID. Objective imported canon records are never edited by an
interaction commit.

This representation requires no Phase 3 schema migration. It avoids depending on
the concurrently developed geography migration and keeps campaign isolation in
every query. Static doctrine metadata is an optional `interactions.json` beside a
pantheon's existing manifest/deity files and is validated at application and bot
startup against the same faith registry.

## Religious context builder

`ReligiousInteractionService.build_context()` returns an immutable,
campaign-scoped `ReligiousInteractionContext`. The existing NPC response context
builder includes its compact prompt block. It contains only the interacting
person's public belief or facts this exact NPC legitimately learned, visible
symbols, the NPC's own profile and religious role, active-pantheon deity
relationships, doctrine entries for the involved deities, relevant episodic
memories, relationship state, temple access, and the current religious location
or event. It never includes the complete pantheon or `full_owner_provided_lore`.

Shared belief permits recognition and ordinary religious vocabulary. The generic
special-interaction evaluator explicitly emits zero automatic trust and no
mechanical effect. A configured rival relationship may produce tension or a
warning, never automatic combat. Personality, culture, interpretation, history,
and existing relationship state remain visible to the NPC response generator, so
two priests of the same deity need not agree or behave alike.

## NPC religious knowledge and secrecy

Private and secret character profiles are structurally absent from NPC context.
`reveal_belief()` or `observe_religious_identity()` records a compact belief fact
for one NPC only, with a typed source: visible symbol, clothing, prior disclosure,
public reputation, temple record, witnessed ritual, shared faction, or player
disclosure. A revelation also creates one idempotent
`RELIGIOUS_REVELATION` memory. It does not make the fact ambient knowledge, and a
different NPC receives nothing automatically.

The real `NPCSocialService` recognizes a narrow set of explicit first-person
English/Thai disclosure phrases only when the message also contains an exact
normalized key, canonical name, Thai name, or approved alias of a deity already
in that character's profile. It commits the learned fact before generating the
NPC response. Merely mentioning or discussing a deity does not reveal a secret.

Callers must provide the committed source event ID. The existing memory service
uses that ID as its retry boundary; repeated Discord delivery cannot create a
second memory or reapply relationship deltas.

## Priest behavior and doctrine

Priest-like roles retain the Phase 2 typed role and knowledge level. The context
therefore distinguishes a priest/theologian/inquisitor from an ordinary cultural
follower without treating either role as a Cleric class. Priests may recognize
known symbols and use the relevant bounded doctrine values in greetings,
questions, warnings, explanations, prayers, or refusals. These are dialogue
affordances, not automatic friendship or service access.

Doctrine is data-driven. A pack may define values plus supported and opposed
committed-event tags in `interactions.json`. Phase 3's Forgotten Realms entries
encode only the doctrine examples supplied by the owner for Tyr, Torm, Selûne,
Shar, Kelemvor, Lathander, Oghma, Silvanus, Tymora, and Bane. Generic evaluation
compares validated witnessed/reported event tags to this metadata; gameplay code
contains no per-deity branches. NPC personal interpretation remains in
`BeliefProfile.personal_interpretation`, allowing disagreement within one faith.

## Religious memories and relationship effects

`record_religious_behavior()` writes only an allowlisted meaningful type through
the existing `NPCMemoryService`: faith revelation or lie, shared prayer, shrine
desecration, temple protection, religious promise or breach, sacred-object
return, priest attack, recruitment refusal, funeral rite, or opposition to
undead. Casual mentions remain ordinary short-lived interactions. A caller may
provide validated relationship deltas, which are clamped through the existing
multi-dimensional relationship model and applied once per source event.

Memory and relationship changes do not rewrite objective canon. Faction
reputation changes are likewise event-idempotent and campaign-scoped, stored as
state records linked to the existing faction entity.

## Temple policies and services

`TemplePolicy` is an owner-approved, typed canonical document linked to an active
deity, an existing campaign location, and optionally an existing campaign
faction. It supports public, member, clergy-only, restricted-archive, sacred
inner-chamber, and emergency-sanctuary areas plus healing, funeral, education,
ritual, donation, lodging, and religious-item-sale service declarations.

Public areas follow public policy. Member and clergy areas validate the
character's profile; archives and inner chambers require explicit persisted
permission. Emergency sanctuary must be enabled by policy. A service must be
declared, available, accessible, and have an owner-approved price.
`purchase_temple_service()` debits the existing `WalletService` ledger with its
normal idempotency key. An empty price is rejected rather than interpreted as a
free healing, ritual, item, or room. Inventory-bearing services still require a
separate existing inventory/shop transaction by the caller; Phase 3 does not
invent items.

## Validated special interactions

The generic evaluator accepts typed trigger inputs from context and committed
event tags. Its typed outcome vocabulary includes dialogue stance, recognition,
proof request, warning, refusal, service availability, access change, memory,
relationship or faction-reputation change, clue disclosure, quest proposal, and
scheduled consequence. The evaluator currently produces only safe dialogue
affordances. Stateful outcomes must be committed by the corresponding validated
service; an LLM proposal is never itself a database mutation.

Major temples or organizations are never generated automatically. Owner/imported
canon must call `register_temple()` with provenance. A small local shrine may be
proposed elsewhere, but that proposal does not become canon through this API.

## Mechanical restrictions

Belief, devotion, recognition, doctrine, reputation, and temple access never
grant spells, slots, ability modifiers, advantage, damage, healing, class
features, blessings, boons, divine intervention, or permanent effects. Paid
"healing service" policy records availability and payment only; an actual healing
effect would require an existing validated spell/effect pipeline. Ao remains a
selectable personal belief and still has `cleric_capable=false`.

## Custom campaign interaction content

A custom pantheon gains the generic shared/rival interaction behavior from its
existing deity relationships. To add doctrine-sensitive behavior, place an
`interactions.json` in that pantheon's content-pack directory. Each entry names a
deity key from the same validated registry, bounded value labels, committed-event
tags it supports/opposes, and provenance. Unknown or duplicate keys fail startup.
Do not place mechanics or unapproved lore in this file.

Phase 3 public APIs future phases must reuse are:

- `ReligiousInteractionService.build_context`
- `reveal_belief` and `observe_religious_identity`
- `evaluate_special_interactions`
- `record_religious_behavior`
- `register_temple` and `decide_temple_access`
- `grant_temple_access`
- `change_faction_reputation`
- `purchase_temple_service`
- `NPCMemoryService.record_typed_memory`

Complex priest conversations, religious quest construction, miracles,
blessings/punishments, divine intervention, religious combat, and a divine-boon
system remain future work.

## Phase 2 architecture decision

Characters and NPCs share one typed `BeliefProfile` value object. The profile is
serialized into a nullable JSON column on each entity instead of being folded into
character identity, NPC personality, or the static rules registry. This keeps an
edit atomic, allows existing rows to migrate as `NULL`, and avoids a second table
whose ownership and campaign scope could drift from the entity it describes.

`BeliefService` is the only write and validation boundary. It resolves every
deity through the Phase 1 `FaithService`, verifies that all referenced pantheons
are active for the entity's campaign, and revalidates stored profiles when they
are read or at startup. A disabled or removed content pack therefore fails with a
clear content error instead of leaking a deity from another campaign.

Personal belief and Cleric mechanics are deliberately separate. Characters have
nullable `cleric_deity_key` and `cleric_domain` fields validated together through
the same service. A Fighter can believe in any active selectable deity; a Cleric's
power source must additionally be Cleric-capable and its domain must be listed by
that deity. Lore never grants mechanics.

NPC religion generation is a bounded proposal service over the same profile. It
uses explicit campaign context and active content, may propose no meaningful
religious identity, and never overwrites imported canon. Religious role determines
knowledge depth but does not assign a character class. Complex dialogue, miracles,
quests, blessings, punishments, and religious combat remain outside Phase 2.

## Phase 1 architecture decision

Faith content is a new versioned content-pack family inside the existing
`app.rules_content` subsystem. It is not campaign canon copied into every
database and it is not a second mechanics registry. A `FaithRegistry` loads,
types, and validates pantheon packs once, using the same Unicode choice-name
normalization already used by spell selection. `FaithService` is the
campaign-aware read boundary over that static registry.

Only activation keys are persisted on `Campaign`. This keeps campaign isolation
explicit: a campaign sees no deity unless its own `active_pantheon_keys` contains
that deity's pantheon. The Forgotten Realms pack is therefore available content,
not a global setting. Static owner-authored lore remains in the versioned pack;
characters, NPCs, factions, and campaign story records are unchanged in Phase 1.

The bundled owner Markdown is retained verbatim beside a small JSON manifest and
structured metadata. The parser derives canonical names, Thai names, alignment,
domains, titles, summaries, and complete per-deity lore sections from that source.
Metadata supplies only stable keys, explicit relationships, flags, and optional
fields that can be traced to the same document. This makes source drift and an
omitted deity validation errors instead of silently losing owner content.

## Pantheon schema

`PantheonDefinition` contains:

- `key`, English and Thai display names, and setting;
- `content_pack_id` and integer `version`;
- ordered `deity_keys`;
- pack-level activation status;
- source reference, provenance, and content status.

`activation_status=AVAILABLE` means the pack may be activated. It does not make
the pantheon active for any campaign.

## Deity schema

`DeityDefinition` contains a stable key and pantheon key, canonical English and
Thai names, aliases, titles, alignment, domains, exact source summary, complete
owner-provided lore, themes, followers, practices, symbols, allies, rivals, enemy
faiths, Cleric and belief-selection flags, public/secret tendency, source
reference, provenance, and implementation status. Optional source fields remain
empty or null; the loader emits warnings rather than inventing values.

Lore is descriptive content only. It never grants a spell, feature, blessing,
domain, or other mechanical effect by itself.

## Campaign activation

`Campaign.active_pantheon_keys` is an explicit, campaign-local list. New and
migrated campaigns start with an empty list. `FaithService.activate_pantheon()`
validates the key before adding it, and `deactivate_pantheon()` removes only that
campaign's activation. Unknown persisted keys fail validation rather than leaking
or silently falling back to another pack.

Future player-facing activation controls must authorize the campaign owner before
calling these methods. Phase 1 intentionally adds no Discord command.

## Resolver behavior

`DeityResolver` indexes stable keys, canonical English and Thai names, configured
aliases, and supplied titles. It reuses the rules-content Unicode normalizer and
adds straight/curly apostrophe equivalence. Spaces, underscores, and hyphens are
equivalent; case folding is Unicode-aware.

Results are typed as `EXACT`, `NORMALIZED_UNIQUE`, `AMBIGUOUS`, or `NOT_FOUND`.
Partial matches are never accepted. Ambiguous normalized references return all
candidate keys and never choose one. Campaign-facing resolution indexes only
deities from that campaign's active pantheons.

## Cleric restrictions

`cleric_capable` is an explicit content flag and is validated independently from
lore. A Cleric-compatible deity must have at least one valid domain. Ao is
`selectable_as_belief=true` for a future owner-permission flow but
`cleric_capable=false`, exactly as the owner source states. Activating the pack is
not itself permission to mutate a character or grant powers.

## Provenance

The bundled Forgotten Realms pack records `OWNER_PROVIDED` provenance and names
`Forgotten_Realms_Pantheon_Detailed.md` as its source. The exact Markdown is stored
with the pack and checked against its owner-source SHA-256 at registry load. No
external lore is merged into it.

## Adding a custom pantheon

Add a directory under `app/rules_content/pantheons/` containing:

1. `manifest.json` with a unique content-pack ID, pantheon definition, source
   filename, and normalized owner-source SHA-256;
2. `deities.json` with one metadata record per source deity;
3. the referenced UTF-8 Markdown source using the documented deity heading and
   Alignment/Domain field format.

Every source heading must map to exactly one metadata record and vice versa. Add
only source-supported aliases and relationships, run the faith validation tests,
then explicitly activate the new pantheon on the intended campaign.

## Public APIs for later phases

Future belief, Cleric, Paladin, NPC, priest, temple, faction, and interaction work
must use `FaithService` for campaign-scoped access:

- `list_active_pantheons`
- `list_selectable_deities`
- `get_deity`
- `resolve_deity_reference`
- `list_cleric_compatible_deities`
- `grants_cleric_powers`
- `list_deity_domains`
- `list_rivals`
- `list_allies`
- `defined_relationship`

They must not read bundled JSON directly or infer mechanics from `full_lore`.

## BeliefProfile (Phase 2)

`app.schemas.belief.BeliefProfile` is the shared immutable value object for a
Character or NPC. It supports optional primary, secondary, and former deity keys;
typed stance, devotion, visibility, religious role, and knowledge level; optional
temple/faction linkage; personal reason and interpretation; sacred symbol,
practices, taboo, doubt, religious conflict, conversion history, owner notes,
source, and provenance. A profile does not require a primary deity. `NULL` means
the person has no recorded or meaningful religious identity and remains valid.

The serialized profile lives in `Character.belief_profile` or
`NPC.belief_profile`. Edits replace only that JSON value and never replace
character identity, NPC personality, memories, relationships, or class data.
`BeliefService` validates every referenced deity against the entity's campaign
and validates a temple/faction link against a same-campaign canonical entity.

## Character creation behavior

After ordinary class choices and spells, Character Creation 2.0 enters a persisted
`belief` step. It first asks whether religion matters and how the character relates
to it; it does not begin by forcing a deity menu. The player may choose a deity,
no meaningful religion, agnostic, atheist, former believer, secret believer, or a
multi-faith profile. Typed deity answers use the Phase 1 campaign resolver.

Direct prose can capture an explicit deity plus obvious stance cues. For example,
"raised in a temple of Tyr but I no longer trust the church" records Tyr as the
former deity and retains the supplied doubt/reason. Follow-up detail is optional.
The final review has an edit-belief action, and the exact substep/profile remains
in the draft so `!rv resume` works after component expiry or restart.

Only pantheons active in that campaign are offered. A campaign with no active
pantheon can still create a non-religious character; a Cleric cannot complete
until legal active content is available.

## Cleric validation

`Character.cleric_deity_key` and `Character.cleric_domain` are nullable mechanical
fields, separate from `belief_profile`. A newly created Cleric must select an
active deity with `cleric_capable=true`, and the selected domain must occur in that
deity's declared domain list. Ao can be a personal/cultural belief but cannot be a
Cleric power source. Fighters and other classes have no mechanical deity gate.
Existing pre-Phase-2 rows migrate with all three new fields null.

## NPC generation and imported canon

`NPCBeliefGenerator.propose()` accepts `NPCBeliefContext` with culture, region,
settlement, profession, class, family, faction, temple connection, personality,
hardship, campaign tone, imported canon, and optional religious role. It returns
a proposal, possibly with no profile. It only assigns a deity when the supplied
context explicitly supports an active deity reference, so an evil deity is never
randomly assigned. Ordinary NPCs are distributed among no meaningful identity,
cultural, questioning, doubtful, agnostic, and atheist profiles rather than being
automatically devout.

Religious role is typed and remains independent from class. Priests,
theologians, inquisitors, and religious officials receive deep knowledge;
acolytes and similar roles receive informed knowledge. A role requiring a deity
needs explicit deity context. `NPCService.create_npc()` accepts either an explicit
profile or this generation context.

Imported NPC belief is stored with `IMPORTED_CANON` source and import provenance.
`BeliefService.set_npc_belief()` enforces source priority: generated proposals
cannot overwrite imported canon, and the conflict is reported instead of silently
changing the NPC.

## Privacy and visibility

`PUBLIC` belief may appear in public identity views. `PRIVATE` and `SECRET`
profiles are omitted entirely for non-owner viewers. The owner's character sheet
may show the full personal profile. `owner_notes` is always owner-only. Mechanical
Cleric deity/domain is rendered separately and is not used to infer or reveal a
secret personal belief.

## APIs Phase 3 must reuse

- `BeliefService.validate_profile`
- `BeliefService.get_character_belief` / `set_character_belief`
- `BeliefService.get_npc_belief` / `set_npc_belief`
- `BeliefService.validate_cleric_mechanics`
- `BeliefService.visible_profile`
- `BeliefService.validate_all_persisted_profiles`
- `NPCBeliefGenerator.propose`
- `knowledge_for_role`
- Phase 1 `FaithService` and `DeityResolver` methods listed above

Phase 3 dialogue or temple systems must consume these validated values. They must
not parse the JSON columns directly, expose private fields, overwrite imported
canon, or infer divine powers from lore or devotion.

## Phase boundaries

Phase 2 stores beliefs, validates new Cleric selections, and provides bounded NPC
belief proposals. It does not add priest dialogue, temples, blessings,
punishments, miracles, divine intervention, religious quests, or religious combat
mechanics.
