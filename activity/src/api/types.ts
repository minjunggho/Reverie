/** TypeScript mirrors of the backend Activity projections.
 * The frontend NEVER recomputes game rules — every derived number arrives here. */

export interface ActivityContext {
  user: { discord_user_id: string; display_name: string };
  campaign: { id: string; name: string } | null;
  membership: { role: string; can_open_dm_studio: boolean } | null;
  character: { id: string; name: string; char_class: string; level: number } | null;
  session: { id: string; number: number; status: string; active: boolean } | null;
  scene: { id: string; location_name: string | null } | null;
  my_campaigns: { id: string; name: string; role: string }[];
}

export interface ResourceView {
  resource_id: string;
  name: string;
  name_th: string;
  current: number;
  max: number;
  recharge: string;
  recharge_th: string;
}

export interface Overview {
  character_id: string;
  name: string;
  level: number;
  char_class: string;
  class_name_th: string;
  planned_subclass: string | null;
  species: string;
  species_name_th: string;
  background: string;
  background_name_th: string;
  concept: string;
  location_name: string | null;
  hp: number;
  max_hp: number;
  temp_hp: number;
  ac: number;
  initiative: number;
  speed: number;
  proficiency_bonus: number;
  hit_die: number;
  hit_dice_remaining: number;
  conditions: string[];
  exhaustion: number;
  dying: boolean;
  stable: boolean;
  dead: boolean;
  death_saves: { successes: number; failures: number };
  concentration: { name: string; spell_key: string | null } | null;
  resources: ResourceView[];
  spellcasting: { save_dc: number; attack_bonus: number; ability: string } | null;
  game_time: number;
  game_time_th: string;
}

export interface BreakdownPart { label: string; value: number; }

export interface SkillView {
  key: string;
  name: string;
  name_th: string;
  ability: string;
  total: number;
  proficiency: "NONE" | "PROFICIENT" | "EXPERTISE";
  breakdown: BreakdownPart[];
  passive: number;
  explain_th: string;
}

export interface AbilityView {
  key: string;
  score: number;
  modifier: number;
  save_total: number;
  save_breakdown: BreakdownPart[];
  save_proficient: boolean;
}

export interface SkillsPayload {
  abilities: AbilityView[];
  skills: SkillView[];
  passive_perception: number;
  proficiency_bonus: number;
}

export interface SpellView {
  key: string;
  kind: string;
  prepared: boolean;
  name: string;
  name_th: string;
  level: number;
  school: string;
  casting_time: string;
  range: string;
  duration: string;
  concentration: boolean;
  ritual: boolean;
  summary_th: string;
  category: string;
  source: string;
}

export interface SpellbookPayload {
  is_caster: boolean;
  ability?: string;
  save_dc?: number;
  attack_bonus?: number;
  prepared_count?: number;
  slots: { level: number; current: number; max: number }[];
  spells: SpellView[];
  concentration: { name: string; spell_key: string | null } | null;
  preparation_editable?: boolean;
}

export interface FeatureEntry {
  key: string;
  grant_type: string;
  name_th: string;
  source_type: string;
  source_th: string;
  source_key: string;
  executable: boolean;
  note_th: string;
  data: Record<string, unknown>;
  resource: ResourceView | null;
}

export interface FeaturesPayload {
  groups: { source_type: string; source_th: string; entries: FeatureEntry[] }[];
}

export interface InventoryPayload {
  items: {
    id: string; name: string; kind: string; quantity: number;
    equipped: boolean; description: string;
  }[];
  count: number;
}

export interface StoryPayload {
  name: string;
  concept: string;
  origin: string;
  desire: string;
  fear: string;
  flaw: string;
  connection: string;
  appearance: string;
  brief: string;
  central_question: string;
  discoveries: { seq: number; summary: string; game_time_th: string }[];
}

export interface PartyMemberView {
  character_id: string;
  name: string;
  player_name: string;
  char_class: string;
  level: number;
  species: string;
  is_you: boolean;
  observable: string[];
  location_name: string | null;
  hp?: number;
  max_hp?: number;
}

export interface ChronicleEntry {
  seq: number;
  event_type: string;
  event_type_th: string;
  summary: string;
  session_id: string | null;
  game_time: number;
  game_time_th: string;
  private: boolean;
}

export interface ChroniclePayload {
  entries: ChronicleEntry[];
  oldest_seq: number | null;
  has_more: boolean;
}

/* ---- DM Studio ---- */

export interface CommandCenterPayload {
  campaign: { id: string; name: string; status: string; central_question: string; session_purpose: string };
  game_time: number;
  game_time_th: string;
  session: { id: string; number: number; status: string; play_state: string } | null;
  scene: { id: string; mode: string; purpose: string; location_name: string | null } | null;
  party: {
    character_id: string; name: string; player_name: string; role: string;
    level: number; char_class: string; hp: number; max_hp: number;
    conditions: string[]; location_id: string | null; location_name: string | null;
  }[];
  threats: { id: string; name: string; goal: string; progress: number; next_action: string; due_game_time: number }[];
  due_events: { id: string; kind: string; due_game_time: number; due_th: string; perceivable: boolean; summary: string }[];
  warnings: string[];
  recent_events: { seq: number; event_type: string; visibility: string; summary: string }[];
}

export interface ScenePayload {
  scene: {
    id: string; mode: string; status: string; purpose: string;
    dramatic_question: string; start_game_time: number; start_game_time_th: string;
    pending_action: string | null; allowed_clues: string[];
    spotlight: Record<string, unknown>;
  } | null;
  location: {
    id: string; name: string; type: string; provenance: string;
    obvious: string; current_activity: string; parent_path: string;
  } | null;
  participants: { ref: string; name: string; hp: number; max_hp: number }[];
  present_npcs: { ref: string; id: string; name: string; communication_mode: string; emotional_state: string }[];
  stale_refs: { ref: string; reason: string }[];
  exits: { label: string; to_name: string; travel_minutes: number; obvious: boolean; access_state: string }[];
  recent_events: { seq: number; event_type: string; visibility: string; summary: string }[];
}

export interface WorldLocation {
  id: string; name: string; type: string; parent_id: string | null;
  provenance: string; obvious: string; focused: string; hidden: string;
  weather: string; current_activity: string;
  npc_count: number; party_here: string[];
  exits: { label: string; to_id: string; to_name: string; travel_minutes: number; obvious: boolean; access_state: string }[];
}

export interface NpcListItem {
  id: string; name: string; location_id: string | null; location_name: string | null;
  communication_mode: string; emotional_state: string; personality: string;
  voice_register: string; goals: string[]; present_in_scene: boolean;
}

export interface NpcDetail {
  npc: {
    id: string; name: string; location_name: string | null;
    communication_mode: string; personality: string; voice_register: string;
    goals: string[]; emotional_state: string; attitudes: Record<string, unknown>;
  };
  knowledge: { subject: string; fact: string; status: string; confidence: number; source: string }[];
  relationships: { entity_ref: string; entity_name: string; attitude: string; trust: number }[];
  protocols: { title: string; rules: string[] }[];
  recent_events: { seq: number; event_type: string; summary: string; visibility: string }[];
}

export interface ThreatsPayload {
  threats: {
    id: string; name: string; goal: string; status: string; progress: number;
    next_action: string; due_game_time: number; due_th: string;
    tick_amount: number; tick_interval: number;
  }[];
  scheduled_events: {
    id: string; kind: string; due_game_time: number; due_th: string;
    perceivable: boolean; resolved: boolean; summary: string;
  }[];
}

export interface ClueView { id: string; text: string; visibility: string; provenance: string; active: boolean; known: boolean; }

export interface SecretsPayload {
  secrets: { id: string; fact: string; visibility: string; revealed: boolean; known_by: string[]; clues: ClueView[] }[];
  unlinked_clues: ClueView[];
  protocols: { id: string; title: string; visibility: string; key: string; rules: string[]; known_by: string[] }[];
}

export interface StudioEvent {
  seq: number; event_type: string; visibility: string;
  actor: string | null; targets: string[];
  game_time: number; game_time_th: string; real_time: string | null;
  summary: string; significance: number;
  mechanical_changes: Record<string, unknown>;
  session_id: string | null; scene_id: string | null;
}

export interface ImportsPayload {
  imports: {
    id: string; filename: string; status: string; content_sha256: string;
    uploaded_at: string | null; uploader: string;
    counts: Record<string, number>; warnings: string[];
  }[];
}
