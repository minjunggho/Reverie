# Faith system

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

## Phase boundaries

Phase 1 does not select or store a character belief, generate NPC religion,
alter dialogue, create priests or temples, grant blessings, create religious
quests, or invoke divine intervention. Those systems must add their own explicit
state and authorization while reusing the registry, resolver, and campaign
activation boundary above.
