/** Mock-mode fixtures — the Last Funeral of God test table.
 * Same TypeScript response types as the real API; dev-only (never production). */
import type {
  ActivityContext, ChroniclePayload, CommandCenterPayload, FeaturesPayload,
  ImportsPayload, InventoryPayload, NpcDetail, NpcListItem, Overview,
  PartyMemberView, ScenePayload, SecretsPayload, SkillsPayload,
  SpellbookPayload, StoryPayload, StudioEvent, ThreatsPayload, WorldLocation,
} from "../api/types";

export const mockContext: ActivityContext = {
  user: { discord_user_id: "mock-user", display_name: "Min" },
  campaign: { id: "camp-1", name: "The Last Funeral of God" },
  membership: { role: "OWNER", can_open_dm_studio: true },
  character: { id: "char-daybell", name: "Daybell", char_class: "wizard", level: 1 },
  session: { id: "sess-1", number: 2, status: "ACTIVE", active: true },
  scene: { id: "scene-1", location_name: "Black Chapel" },
  my_campaigns: [{ id: "camp-1", name: "The Last Funeral of God", role: "OWNER" }],
};

export const mockOverview: Overview = {
  character_id: "char-daybell",
  name: "Daybell",
  level: 1,
  char_class: "wizard",
  class_name_th: "จอมเวท",
  planned_subclass: "diviner",
  species: "human",
  species_name_th: "มนุษย์",
  background: "sage",
  background_name_th: "ปราชญ์",
  concept: "อดีตอาลักษณ์ที่ขโมยตำราต้องห้ามจากหอเก็บของศาสนจักร",
  location_name: "Black Chapel",
  hp: 4,
  max_hp: 7,
  temp_hp: 2,
  ac: 12,
  initiative: 2,
  speed: 30,
  proficiency_bonus: 2,
  hit_die: 6,
  hit_dice_remaining: 1,
  conditions: ["หวาดกลัว"],
  exhaustion: 0,
  dying: false,
  stable: false,
  dead: false,
  death_saves: { successes: 0, failures: 0 },
  concentration: { name: "Detect Magic", spell_key: "detect magic" },
  resources: [
    { resource_id: "resource:arcane_recovery", name: "Arcane Recovery", name_th: "ฟื้นพลังเวท",
      current: 1, max: 1, recharge: "long_rest", recharge_th: "พักยาว" },
    { resource_id: "resource:spell_slots_1", name: "Spell Slots (1st)", name_th: "ช่องเวทระดับ 1",
      current: 1, max: 2, recharge: "long_rest", recharge_th: "พักยาว" },
  ],
  spellcasting: { save_dc: 13, attack_bonus: 5, ability: "int" },
  game_time: 540,
  game_time_th: "วันแรก · 09:00 น.",
};

const B = (parts: [string, number][]) => parts.map(([label, value]) => ({ label, value }));

export const mockSkills: SkillsPayload = {
  proficiency_bonus: 2,
  passive_perception: 11,
  abilities: [
    { key: "str", score: 8, modifier: -1, save_total: -1, save_breakdown: B([["STR", -1]]), save_proficient: false },
    { key: "dex", score: 14, modifier: 2, save_total: 2, save_breakdown: B([["DEX", 2]]), save_proficient: false },
    { key: "con", score: 12, modifier: 1, save_total: 1, save_breakdown: B([["CON", 1]]), save_proficient: false },
    { key: "int", score: 16, modifier: 3, save_total: 5, save_breakdown: B([["INT", 3], ["Proficiency", 2]]), save_proficient: true },
    { key: "wis", score: 13, modifier: 1, save_total: 3, save_breakdown: B([["WIS", 1], ["Proficiency", 2]]), save_proficient: true },
    { key: "cha", score: 10, modifier: 0, save_total: 0, save_breakdown: B([["CHA", 0]]), save_proficient: false },
  ],
  skills: [
    { key: "acrobatics", name: "acrobatics", name_th: "ผาดโผน", ability: "dex", total: 2, proficiency: "NONE", breakdown: B([["DEX", 2]]), passive: 12, explain_th: "การทรงตัว หลบหลีก และเคลื่อนไหวผาดโผน" },
    { key: "animal_handling", name: "animal_handling", name_th: "การจัดการสัตว์", ability: "wis", total: 1, proficiency: "NONE", breakdown: B([["WIS", 1]]), passive: 11, explain_th: "การอ่านและสงบสัตว์" },
    { key: "arcana", name: "arcana", name_th: "ศาสตร์เวท", ability: "int", total: 5, proficiency: "PROFICIENT", breakdown: B([["INT", 3], ["Proficiency", 2]]), passive: 15, explain_th: "ความรู้เรื่องเวทมนตร์ สัญลักษณ์ และปรากฏการณ์เหนือธรรมชาติ" },
    { key: "athletics", name: "athletics", name_th: "กำลังกาย", ability: "str", total: -1, proficiency: "NONE", breakdown: B([["STR", -1]]), passive: 9, explain_th: "การปีน กระโดด ว่ายน้ำ และการใช้กำลัง" },
    { key: "deception", name: "deception", name_th: "ลวงหลอก", ability: "cha", total: 0, proficiency: "NONE", breakdown: B([["CHA", 0]]), passive: 10, explain_th: "การพูดให้คนเชื่อในสิ่งที่ไม่จริง" },
    { key: "history", name: "history", name_th: "ประวัติศาสตร์", ability: "int", total: 5, proficiency: "PROFICIENT", breakdown: B([["INT", 3], ["Proficiency", 2]]), passive: 15, explain_th: "ความรู้เหตุการณ์และตำนานในอดีต" },
    { key: "insight", name: "insight", name_th: "อ่านใจ", ability: "wis", total: 1, proficiency: "NONE", breakdown: B([["WIS", 1]]), passive: 11, explain_th: "การอ่านเจตนาที่แท้จริงของผู้อื่น" },
    { key: "intimidation", name: "intimidation", name_th: "ข่มขู่", ability: "cha", total: 0, proficiency: "NONE", breakdown: B([["CHA", 0]]), passive: 10, explain_th: "การกดดันด้วยคำพูดหรือท่าที" },
    { key: "investigation", name: "investigation", name_th: "สืบค้น", ability: "int", total: 7, proficiency: "EXPERTISE", breakdown: B([["INT", 3], ["Expertise", 4]]), passive: 17, explain_th: "การวิเคราะห์เบาะแสและหาความเชื่อมโยง" },
    { key: "medicine", name: "medicine", name_th: "รักษา", ability: "wis", total: 1, proficiency: "NONE", breakdown: B([["WIS", 1]]), passive: 11, explain_th: "การปฐมพยาบาลและวินิจฉัยอาการ" },
    { key: "nature", name: "nature", name_th: "ธรรมชาติ", ability: "int", total: 3, proficiency: "NONE", breakdown: B([["INT", 3]]), passive: 13, explain_th: "ความรู้เรื่องพืช สัตว์ และภูมิประเทศ" },
    { key: "perception", name: "perception", name_th: "สังเกต", ability: "wis", total: 1, proficiency: "NONE", breakdown: B([["WIS", 1]]), passive: 11, explain_th: "การสังเกตเห็นสิ่งรอบตัว" },
    { key: "performance", name: "performance", name_th: "การแสดง", ability: "cha", total: 0, proficiency: "NONE", breakdown: B([["CHA", 0]]), passive: 10, explain_th: "การแสดงต่อหน้าผู้ชม" },
    { key: "persuasion", name: "persuasion", name_th: "โน้มน้าว", ability: "cha", total: 0, proficiency: "NONE", breakdown: B([["CHA", 0]]), passive: 10, explain_th: "การพูดโน้มน้าวด้วยเหตุผลหรือเสน่ห์" },
    { key: "religion", name: "religion", name_th: "ศาสนา", ability: "int", total: 5, proficiency: "PROFICIENT", breakdown: B([["INT", 3], ["Proficiency", 2]]), passive: 15, explain_th: "ความรู้เรื่องเทพ พิธีกรรม และคำสอน" },
    { key: "sleight_of_hand", name: "sleight_of_hand", name_th: "มือไว", ability: "dex", total: 2, proficiency: "NONE", breakdown: B([["DEX", 2]]), passive: 12, explain_th: "การใช้มืออย่างแนบเนียน" },
    { key: "stealth", name: "stealth", name_th: "ย่องเงียบ", ability: "dex", total: 2, proficiency: "NONE", breakdown: B([["DEX", 2]]), passive: 12, explain_th: "การเคลื่อนไหวโดยไม่ให้ใครเห็น" },
    { key: "survival", name: "survival", name_th: "เอาตัวรอด", ability: "wis", total: 1, proficiency: "NONE", breakdown: B([["WIS", 1]]), passive: 11, explain_th: "การตามรอย หาอาหาร และนำทาง" },
  ],
};

export const mockSpellbook: SpellbookPayload = {
  is_caster: true,
  ability: "int",
  save_dc: 13,
  attack_bonus: 5,
  prepared_count: 4,
  slots: [{ level: 1, current: 1, max: 2 }],
  concentration: { name: "Detect Magic", spell_key: "detect magic" },
  preparation_editable: false,
  spells: [
    { key: "fire bolt", kind: "cantrip", prepared: true, name: "Fire Bolt", name_th: "ลูกไฟพุ่ง", level: 0, school: "evocation", casting_time: "action", range: "120 ฟุต", duration: "ทันที", concentration: false, ritual: false, summary_th: "ยิงลูกไฟ 1d10 ใส่เป้าหมายเดียว", category: "โจมตี", source: "CLASS" },
    { key: "mage hand", kind: "cantrip", prepared: true, name: "Mage Hand", name_th: "มือเวท", level: 0, school: "conjuration", casting_time: "action", range: "30 ฟุต", duration: "1 นาที", concentration: false, ritual: false, summary_th: "มือล่องหนหยิบจับของเบาๆ จากระยะไกล", category: "ใช้งาน", source: "CLASS" },
    { key: "light", kind: "cantrip", prepared: true, name: "Light", name_th: "แสงสว่าง", level: 0, school: "evocation", casting_time: "action", range: "สัมผัส", duration: "1 ชั่วโมง", concentration: false, ritual: false, summary_th: "วัตถุเรืองแสงสว่าง 20 ฟุต", category: "สำรวจ", source: "CLASS" },
    { key: "detect magic", kind: "book", prepared: true, name: "Detect Magic", name_th: "สัมผัสเวท", level: 1, school: "divination", casting_time: "action", range: "ตัวเอง", duration: "เพ่งสมาธิ 10 นาที", concentration: true, ritual: true, summary_th: "รับรู้เวทมนตร์ในระยะ 30 ฟุต", category: "สำรวจ", source: "CLASS" },
    { key: "shield", kind: "book", prepared: true, name: "Shield", name_th: "โล่เวท", level: 1, school: "abjuration", casting_time: "reaction", range: "ตัวเอง", duration: "1 รอบ", concentration: false, ritual: false, summary_th: "+5 AC ชั่วขณะเมื่อถูกโจมตี", category: "ป้องกัน", source: "CLASS" },
    { key: "sleep", kind: "book", prepared: false, name: "Sleep", name_th: "มนตร์หลับ", level: 1, school: "enchantment", casting_time: "action", range: "90 ฟุต", duration: "1 นาที", concentration: false, ritual: false, summary_th: "สะกดสิ่งมีชีวิต HP รวม 5d8 ให้หลับ", category: "ควบคุม", source: "CLASS" },
    { key: "identify", kind: "book", prepared: false, name: "Identify", name_th: "พิเคราะห์ของเวท", level: 1, school: "divination", casting_time: "1m", range: "สัมผัส", duration: "ทันที", concentration: false, ritual: true, summary_th: "รู้คุณสมบัติของไอเทมเวทหนึ่งชิ้น", category: "สำรวจ", source: "CLASS" },
  ],
};

export const mockFeatures: FeaturesPayload = {
  groups: [
    {
      source_type: "CLASS", source_th: "คลาส",
      entries: [
        { key: "spellcasting", grant_type: "feature", name_th: "การร่ายเวท", source_type: "CLASS", source_th: "คลาส", source_key: "class:wizard", executable: true, note_th: "", data: {}, resource: null },
        { key: "ritual_adept", grant_type: "feature", name_th: "ผู้ชำนาญพิธี", source_type: "CLASS", source_th: "คลาส", source_key: "class:wizard", executable: true, note_th: "", data: {}, resource: null },
        { key: "arcane_recovery", grant_type: "feature", name_th: "ฟื้นพลังเวท", source_type: "CLASS", source_th: "คลาส", source_key: "class:wizard", executable: true, note_th: "", data: {}, resource: { resource_id: "resource:arcane_recovery", name: "Arcane Recovery", name_th: "ฟื้นพลังเวท", current: 1, max: 1, recharge: "long_rest", recharge_th: "พักยาว" } },
      ],
    },
    {
      source_type: "SPECIES", source_th: "เผ่า",
      entries: [
        { key: "resourceful", grant_type: "trait", name_th: "ไหวพริบเอาตัวรอด", source_type: "SPECIES", source_th: "เผ่า", source_key: "species:human", executable: false, note_th: "บันทึกไว้ — กลไกใช้งานในสไลซ์ถัดไป", data: {}, resource: null },
        { key: "skillful", grant_type: "trait", name_th: "หัวไว", source_type: "SPECIES", source_th: "เผ่า", source_key: "species:human", executable: true, note_th: "", data: {}, resource: null },
      ],
    },
    {
      source_type: "BACKGROUND", source_th: "ภูมิหลัง",
      entries: [
        { key: "Magic Initiate (Wizard)", grant_type: "feat", name_th: "Magic Initiate (Wizard)", source_type: "BACKGROUND", source_th: "ภูมิหลัง", source_key: "background:sage", executable: false, note_th: "บันทึกไว้ — กลไกใช้งานในสไลซ์ถัดไป", data: {}, resource: null },
      ],
    },
  ],
};

export const mockInventory: InventoryPayload = {
  count: 5,
  items: [
    { id: "i1", name: "ไม้เท้าเวท", kind: "weapon", quantity: 1, equipped: true, description: "ไม้เท้าโอ๊คเก่า ปลายหุ้มทองแดง" },
    { id: "i2", name: "ตำราคาถา", kind: "gear", quantity: 1, equipped: false, description: "ตำราหนังหุ้มบันทึกคาถาทั้งหมดของ Daybell" },
    { id: "i3", name: "ตำราเก่าหนึ่งเล่ม", kind: "gear", quantity: 1, equipped: false, description: "เล่มที่ขโมยมาจากหอเก็บ — หน้าสุดท้ายหายไป" },
    { id: "i4", name: "ชุดอาลักษณ์", kind: "gear", quantity: 1, equipped: false, description: "ปากกา หมึก และกระดาษหนัง" },
    { id: "i5", name: "เหรียญทอง", kind: "treasure", quantity: 12, equipped: false, description: "" },
  ],
};

export const mockStory: StoryPayload = {
  name: "Daybell",
  concept: "อดีตอาลักษณ์ที่ขโมยตำราต้องห้ามจากหอเก็บของศาสนจักร",
  origin: "โตในหอเก็บเอกสารของมหาวิหาร ท่ามกลางกลิ่นหมึกและความลับ",
  desire: "อยากรู้ว่าหน้าสุดท้ายof ตำราที่หายไปเขียนว่าอะไร",
  fear: "กลัวว่าศาสนจักรจะรู้ว่าใครเป็นคนขโมย",
  flaw: "เชื่อว่าความรู้ทุกอย่างควรถูกเปิดเผย ไม่ว่าราคาจะเป็นเท่าไร",
  connection: "Aria เคยช่วยเขาหนีออกจากเขตมหาวิหารเมื่อปีก่อน",
  appearance: "ชายหนุ่มผอมสูง นิ้วเปื้อนหมึกถาวร แว่นสายตาร้าวมุมหนึ่ง",
  brief: "สิบเจ็ดปีก่อน พระเจ้าสิ้นลม ปาฏิหาริย์กลายเป็นของหายาก และศาสนจักรควบคุมพิธีฝังศพ",
  central_question: "ความจริงเรื่องการตายของพระเจ้าจะถูกฝังไปตลอดกาล หรือจะมีใครขุดมันขึ้นมา?",
  discoveries: [
    { seq: 41, summary: "เอกสารชุดนั้นควรถูกเผาไปแล้ว", game_time_th: "วันแรก · 08:10 น." },
  ],
};

export const mockParty: { members: PartyMemberView[] } = {
  members: [
    { character_id: "char-daybell", name: "Daybell", player_name: "Min", char_class: "wizard", level: 1, species: "human", is_you: true, observable: ["หวาดกลัว"], location_name: "Black Chapel", hp: 4, max_hp: 7 },
    { character_id: "char-aria", name: "Aria", player_name: "Mai", char_class: "rogue", level: 1, species: "halfling", is_you: false, observable: [], location_name: "Black Chapel" },
    { character_id: "char-veskan", name: "Veskan", player_name: "Nick", char_class: "fighter", level: 1, species: "dwarf", is_you: false, observable: ["บาดเจ็บหนัก"], location_name: "Chapel Road" },
  ],
};

export const mockChronicle: ChroniclePayload = {
  has_more: false,
  oldest_seq: 1,
  entries: [
    { seq: 1, event_type: "SESSION_STARTED", event_type_th: "เริ่มเซสชัน", summary: "เริ่มเซสชันที่ 1: งานศพครั้งสุดท้าย", session_id: "sess-0", game_time: 0, game_time_th: "วันแรก · 00:00 น.", private: false },
    { seq: 8, event_type: "CHARACTER_MOVED", event_type_th: "การเดินทาง", summary: "ปาร์ตี้มาถึง Black Chapel", session_id: "sess-0", game_time: 300, game_time_th: "วันแรก · 05:00 น.", private: false },
    { seq: 22, event_type: "PLAYER_ACTION_COMMITTED", event_type_th: "การกระทำ", summary: "Daybell ถาม Mother Veyra เรื่องกฎห้าข้อ", session_id: "sess-1", game_time: 480, game_time_th: "วันแรก · 08:00 น.", private: false },
    { seq: 41, event_type: "KNOWLEDGE_GAINED", event_type_th: "ได้รู้บางอย่าง", summary: "เอกสารชุดนั้นควรถูกเผาไปแล้ว", session_id: "sess-1", game_time: 490, game_time_th: "วันแรก · 08:10 น.", private: true },
    { seq: 55, event_type: "WORLD_TIME_ADVANCED", event_type_th: "เวลาผ่านไป", summary: "ระฆังมหาวิหารตีเองโดยไม่มีใครสั่ง", session_id: "sess-1", game_time: 540, game_time_th: "วันแรก · 09:00 น.", private: false },
  ],
};

/* ---- DM Studio fixtures ---- */

export const mockCommandCenter: CommandCenterPayload = {
  campaign: { id: "camp-1", name: "The Last Funeral of God", status: "ACTIVE", central_question: "ความจริงเรื่องการตายของพระเจ้าจะถูกฝังไปตลอดกาล หรือจะมีใครขุดมันขึ้นมา?", session_purpose: "แนะนำกฎห้าข้อของขบวนศพ และความผิดปกติของระฆัง" },
  game_time: 540,
  game_time_th: "วันแรก · 09:00 น.",
  session: { id: "sess-1", number: 2, status: "ACTIVE", play_state: "TABLE_OPEN" },
  scene: { id: "scene-1", mode: "SOCIAL", purpose: "เฝ้าโลงศพ", location_name: "Black Chapel" },
  party: [
    { character_id: "char-daybell", name: "Daybell", player_name: "Min", role: "OWNER", level: 1, char_class: "wizard", hp: 4, max_hp: 7, conditions: ["หวาดกลัว"], location_id: "loc-chapel", location_name: "Black Chapel" },
    { character_id: "char-aria", name: "Aria", player_name: "Mai", role: "PLAYER", level: 1, char_class: "rogue", hp: 9, max_hp: 9, conditions: [], location_id: "loc-chapel", location_name: "Black Chapel" },
  ],
  threats: [
    { id: "t1", name: "The Failing Seal", goal: "ตราผนึกใต้เมืองอ่อนกำลังลง", progress: 35, next_action: "รอยร้าวแรกปรากฏในสุสานหลวง", due_game_time: 720 },
    { id: "t2", name: "The Last Church", goal: "รักษาความลับเรื่องการตายของพระเจ้า", progress: 20, next_action: "ส่งผู้สืบสวนไปที่โรงเตี๊ยม", due_game_time: 600 },
  ],
  due_events: [
    { id: "we1", kind: "bell_toll", due_game_time: 600, due_th: "วันแรก · 10:00 น.", perceivable: true, summary: "ระฆังจะตีเองอีกครั้ง" },
  ],
  warnings: ["NPC 'Sister Nara' อยู่ในรายชื่อฉากแต่ตำแหน่งจริงไม่ตรง — จะไม่ถูกแสดงว่าอยู่ในฉาก"],
  recent_events: [
    { seq: 22, event_type: "PLAYER_ACTION_COMMITTED", visibility: "PARTY", summary: "Daybell ถาม Mother Veyra เรื่องกฎห้าข้อ" },
    { seq: 31, event_type: "NPC_STATE_CHANGED", visibility: "DM_ONLY", summary: "Mother Veyra เริ่มจับตาดู Daybell" },
    { seq: 55, event_type: "WORLD_TIME_ADVANCED", visibility: "PARTY", summary: "ระฆังมหาวิหารตีเองโดยไม่มีใครสั่ง" },
  ],
};

export const mockScene: ScenePayload = {
  scene: { id: "scene-1", mode: "SOCIAL", status: "ACTIVE", purpose: "เฝ้าโลงศพ", dramatic_question: "ปาร์ตี้จะรักษากฎห้าข้อได้ตลอดคืนหรือไม่", start_game_time: 480, start_game_time_th: "วันแรก · 08:00 น.", pending_action: null, allowed_clues: ["เอกสารชุดนั้นควรถูกเผาไปแล้ว", "ระฆังตีเองในเวลาที่ไม่มีใครสั่ง"], spotlight: { last_actor: "character:char-daybell" } },
  location: { id: "loc-chapel", name: "Black Chapel", type: "LOCATION", provenance: "IMPORTED", obvious: "โบสถ์เล็กหินดำ เทียนไขจุดรอบโลงศพกลางห้อง โซ่เงินพันรอบฝาโลงไว้แน่นหนา", current_activity: "แม่ชีสวดเบาๆ รอบโลง", parent_path: "Veyr · Cathedral District" },
  participants: [
    { ref: "character:char-daybell", name: "Daybell", hp: 4, max_hp: 7 },
    { ref: "character:char-aria", name: "Aria", hp: 9, max_hp: 9 },
  ],
  present_npcs: [
    { ref: "npc:n1", id: "n1", name: "Mother Veyra", communication_mode: "SPOKEN", emotional_state: "ระแวง" },
    { ref: "npc:n2", id: "n2", name: "Father Caldus", communication_mode: "SPOKEN", emotional_state: "calm" },
  ],
  stale_refs: [{ ref: "npc:n3", reason: "'Sister Nara' ตำแหน่งจริงไม่ตรงกับฉาก" }],
  exits: [
    { label: "ประตูหน้า", to_name: "Chapel Road", travel_minutes: 5, obvious: true, access_state: "open" },
  ],
  recent_events: [
    { seq: 22, event_type: "PLAYER_ACTION_COMMITTED", visibility: "PARTY", summary: "Daybell ถาม Mother Veyra เรื่องกฎห้าข้อ" },
  ],
};

export const mockWorld: { locations: WorldLocation[] } = {
  locations: [
    { id: "loc-veyr", name: "Veyr", type: "REGION", parent_id: null, provenance: "IMPORTED", obvious: "นครหลวงเวย์ริ เมืองที่ระฆังไม่เคยหยุดตี", focused: "", hidden: "", weather: "", current_activity: "", npc_count: 0, party_here: [], exits: [] },
    { id: "loc-cd", name: "Cathedral District", type: "DISTRICT", parent_id: "loc-veyr", provenance: "IMPORTED", obvious: "ลานกว้างหน้ามหาวิหารหินสีเทา", focused: "", hidden: "", weather: "ฝนปรอย", current_activity: "", npc_count: 0, party_here: [], exits: [] },
    { id: "loc-chapel", name: "Black Chapel", type: "LOCATION", parent_id: "loc-cd", provenance: "IMPORTED", obvious: "โบสถ์เล็กหินดำ เทียนไขจุดรอบโลงศพกลางห้อง", focused: "รอยขีดเล็กๆ ที่ขอบโลงดูใหม่กว่าโซ่", hidden: "ใต้แท่นบูชามีช่องลับ", weather: "", current_activity: "แม่ชีสวดเบาๆ รอบโลง", npc_count: 3, party_here: ["Daybell", "Aria"], exits: [{ label: "ประตูหน้า", to_id: "loc-road", to_name: "Chapel Road", travel_minutes: 5, obvious: true, access_state: "open" }] },
    { id: "loc-road", name: "Chapel Road", type: "DISTRICT", parent_id: "loc-cd", provenance: "IMPORTED", obvious: "ถนนหินเรียบรอบมหาวิหาร ผู้แสวงบุญเดินเป็นแถวเงียบๆ", focused: "", hidden: "", weather: "", current_activity: "", npc_count: 0, party_here: [], exits: [{ label: "กลับ", to_id: "loc-chapel", to_name: "Black Chapel", travel_minutes: 5, obvious: true, access_state: "open" }] },
    { id: "loc-forge", name: "ร้านตีเหล็กปลายถนน", type: "SHOP", parent_id: "loc-cd", provenance: "AI_EXPANDED", obvious: "ร้านแคบๆ เบียดอยู่ระหว่างอาคารสองหลัง", focused: "", hidden: "", weather: "", current_activity: "", npc_count: 1, party_here: [], exits: [] },
  ],
};

export const mockNpcs: { npcs: NpcListItem[] } = {
  npcs: [
    { id: "n1", name: "Mother Veyra", location_id: "loc-chapel", location_name: "Black Chapel", communication_mode: "SPOKEN", emotional_state: "ระแวง", personality: "เย็นชา หนักแน่น ไม่ยอมเผยอารมณ์", voice_register: "ต่ำ ชัด ช้า", goals: ["คุ้มกันขบวนโลงศพให้ถึงที่หมายโดยไม่มีใครฝ่าฝืนกฎห้าข้อ"], present_in_scene: true },
    { id: "n2", name: "Father Caldus", location_id: "loc-chapel", location_name: "Black Chapel", communication_mode: "SPOKEN", emotional_state: "calm", personality: "พูดน้อย เฝ้าประตู", voice_register: "ต่ำ ช้า", goals: ["เฝ้าประตูโบสถ์และคอยเตือนกฎห้าข้อแก่ผู้มาใหม่"], present_in_scene: true },
    { id: "n3", name: "Sister Nara", location_id: "loc-road", location_name: "Chapel Road", communication_mode: "SLATE", emotional_state: "calm", personality: "เงียบสนิท จดบันทึกทุกอย่าง", voice_register: "—", goals: ["จดบันทึกทุกคำพูดที่เกิดขึ้นใกล้โลงศพ"], present_in_scene: false },
  ],
};

export const mockNpcDetail: NpcDetail = {
  npc: { id: "n1", name: "Mother Veyra", location_name: "Black Chapel", communication_mode: "SPOKEN", personality: "เย็นชา หนักแน่น ไม่ยอมเผยอารมณ์", voice_register: "ต่ำ ชัด ช้า", goals: ["คุ้มกันขบวนโลงศพให้ถึงที่หมายโดยไม่มีใครฝ่าฝืนกฎห้าข้อ"], emotional_state: "ระแวง", attitudes: { suspicion_level: 2 } },
  knowledge: [
    { subject: "daybell", fact: "Daybell ถามเรื่องกฎบ่อยผิดปกติ", status: "SUSPECTS", confidence: 0.7, source: "social:character:char-daybell" },
    { subject: "coffin", fact: "โลงเคยส่งเสียงครั้งหนึ่งเมื่อสามคืนก่อน", status: "KNOWS", confidence: 1, source: "canon" },
  ],
  relationships: [
    { entity_ref: "character:char-daybell", entity_name: "Daybell", attitude: "wary", trust: -1 },
  ],
  protocols: [
    { title: "กฎห้าข้อของขบวนศพ", rules: ["คุ้มกันโลงศพ", "ห้ามเปิดโลง", "ห้ามให้ใครแตะต้องโลง", "หากนักบวชตาย ให้เดินทางต่อ", "หากโลงพูด ห้ามตอบ"] },
  ],
  recent_events: [
    { seq: 31, event_type: "NPC_STATE_CHANGED", summary: "Mother Veyra เริ่มจับตาดู Daybell", visibility: "DM_ONLY" },
  ],
};

export const mockThreats: ThreatsPayload = {
  threats: [
    { id: "t1", name: "The Failing Seal", goal: "ตราผนึกใต้เมืองอ่อนกำลังลง", status: "active", progress: 35, next_action: "รอยร้าวแรกปรากฏในสุสานหลวง", due_game_time: 720, due_th: "วันแรก · 12:00 น.", tick_amount: 10, tick_interval: 240 },
    { id: "t2", name: "The Last Church", goal: "รักษาความลับเรื่องการตายของพระเจ้า", status: "active", progress: 20, next_action: "ส่งผู้สืบสวนไปที่โรงเตี๊ยม", due_game_time: 600, due_th: "วันแรก · 10:00 น.", tick_amount: 5, tick_interval: 480 },
  ],
  scheduled_events: [
    { id: "we1", kind: "bell_toll", due_game_time: 600, due_th: "วันแรก · 10:00 น.", perceivable: true, resolved: false, summary: "ระฆังจะตีเองอีกครั้ง" },
  ],
};

export const mockSecrets: SecretsPayload = {
  secrets: [
    {
      id: "s1", fact: "พระเจ้าไม่ได้ตาย งานศพผนึกพระองค์ไว้ใต้นครหลวง", visibility: "DM_ONLY", revealed: false, known_by: [],
      clues: [
        { id: "c1", text: "เอกสารชุดนั้นควรถูกเผาไปแล้ว", visibility: "DM_ONLY", provenance: "IMPORTED_CANON", active: true, known: true },
        { id: "c2", text: "ระฆังตีเองในเวลาที่ไม่มีใครสั่ง", visibility: "DM_ONLY", provenance: "IMPORTED_CANON", active: true, known: false },
      ],
    },
    {
      id: "s2", fact: "ระฆังเชื่อมกับสิ่งที่อยู่ใต้เมือง", visibility: "DM_ONLY", revealed: false, known_by: [],
      clues: [
        { id: "c3", text: "เสียงระฆังทำให้สุนัขเงียบทั้งย่าน", visibility: "DM_ONLY", provenance: "IMPORTED_CANON", active: true, known: false },
      ],
    },
  ],
  unlinked_clues: [],
  protocols: [
    { id: "p1", title: "กฎห้าข้อของขบวนศพ", visibility: "PARTY", key: "coffin-escort-five-rules", rules: ["คุ้มกันโลงศพ", "ห้ามเปิดโลง", "ห้ามให้ใครแตะต้องโลง", "หากนักบวชตาย ให้เดินทางต่อ", "หากโลงพูด ห้ามตอบ"], known_by: ["Mother Veyra", "Father Caldus", "Sister Nara"] },
  ],
};

export const mockEvents: { total: number; events: StudioEvent[] } = {
  total: 55,
  events: [
    { seq: 22, event_type: "PLAYER_ACTION_COMMITTED", visibility: "PARTY", actor: "character:char-daybell", targets: ["npc:n1"], game_time: 480, game_time_th: "วันแรก · 08:00 น.", real_time: "2026-07-11T13:00:00Z", summary: "Daybell ถาม Mother Veyra เรื่องกฎห้าข้อ", significance: 15, mechanical_changes: {}, session_id: "sess-1", scene_id: "scene-1" },
    { seq: 31, event_type: "NPC_STATE_CHANGED", visibility: "DM_ONLY", actor: "npc:n1", targets: ["character:char-daybell"], game_time: 485, game_time_th: "วันแรก · 08:05 น.", real_time: "2026-07-11T13:05:00Z", summary: "Mother Veyra เริ่มจับตาดู Daybell", significance: 30, mechanical_changes: { suspicion: { from: 1, to: 2 } }, session_id: "sess-1", scene_id: "scene-1" },
    { seq: 41, event_type: "KNOWLEDGE_GAINED", visibility: "PLAYER_ONLY", actor: "character:char-daybell", targets: [], game_time: 490, game_time_th: "วันแรก · 08:10 น.", real_time: "2026-07-11T13:10:00Z", summary: "เอกสารชุดนั้นควรถูกเผาไปแล้ว", significance: 40, mechanical_changes: {}, session_id: "sess-1", scene_id: "scene-1" },
    { seq: 55, event_type: "WORLD_TIME_ADVANCED", visibility: "PARTY", actor: "system", targets: [], game_time: 540, game_time_th: "วันแรก · 09:00 น.", real_time: "2026-07-11T14:00:00Z", summary: "ระฆังมหาวิหารตีเองโดยไม่มีใครสั่ง", significance: 20, mechanical_changes: {}, session_id: "sess-1", scene_id: null },
  ],
};

export const mockImports: ImportsPayload = {
  imports: [
    {
      id: "imp-2", filename: "last_funeral_v2.md", status: "PENDING_REVIEW",
      content_sha256: "9f2ab04c11de", uploaded_at: "2026-07-11T12:40:00Z", uploader: "Min",
      counts: { locations: 7, important_npcs: 6, secrets: 2, clues: 4, protocols: 1, threats: 1 },
      warnings: ["NPC 'Mother Seraphine' has no canonical current location."],
    },
    {
      id: "imp-1", filename: "last_funeral_of_god.md", status: "APPROVED",
      content_sha256: "77aa19c003bd", uploaded_at: "2026-07-10T09:00:00Z", uploader: "Min",
      counts: { locations: 5, important_npcs: 3, secrets: 2, clues: 4, threats: 1 },
      warnings: [],
    },
  ],
};
