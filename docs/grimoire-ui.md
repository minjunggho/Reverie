# Grimoire UI (E6)

The player surface of the Reverie Activity. Design language: dark fantasy,
refined, calm — charcoal-blue background, layered slate surfaces, aged-gold
primary accent, silver-rain-blue secondary, ritual red for danger, moss green
for success. Thai-first typography (`Noto Sans Thai`/`Sarabun`/`Leelawadee UI`
stack; serif display face for headings). No parchment textures, no stock art,
no emoji-as-design-system; sigils are abstract glyphs/initials.

## Screens

| View | Answers | Key elements |
|---|---|---|
| **Overview** | Who am I? How healthy? What can I do now? What affects me? | identity line, concept, location + game time, HP bar (temp-HP hatch, hurt/critical tones), condition/death-save chips, concentration banner, stat medallions (AC/init/speed/prof/hit dice/DC), resource trackers with pips → provenance sheet |
| **Skills** | ability scores, saves, all 18 skills | sort by name/bonus, proficient-only filter, ● save proficiency, ถนัด/เชี่ยวชาญ badges, tap → real `Breakdown` (INT +3, Proficiency +2 = +5) + passive |
| **Spellbook** | casting numbers + the book | DC/attack/ability header, slot pips, filters (prepared/cantrip/concentration/ritual), level-grouped list, detail sheet with concise Thai mechanics; **read-only** — re-preparation happens after a long rest in Discord |
| **Features** | what I can do and where it came from | grouped by provenance (คลาส/เผ่า/ภูมิหลัง/…), พร้อมใช้ / ใช้หมดแล้ว / บันทึกไว้-กลไกยังไม่รองรับ statuses, linked resource pips |
| **Inventory** | what I carry | search + kind filter, quantity, สวมใส่อยู่, descriptions |
| **Story** | who my character is | concept lead, hook fields, world brief + central question, private discoveries timeline (PLAYER_ONLY, witness-filtered server-side) |
| **Party** | who travels with me | observable state only — other players' exact HP is not in the payload |
| **Chronicle** | what has happened | session-grouped journal timeline, filters, "เฉพาะเจ้า" markers on private entries |

## States

Every screen implements loading skeletons, error-with-retry, and Thai empty
states ("ยังไม่มีตัวละครในแคมเปญนี้ — เริ่มสร้างตัวละครใน Discord ด้วย `!rv character`").
Full-screen phases: booting, outside-Discord fallback, session-expired,
no-campaign (with the user's own campaign list only).

## Accessibility

44px touch targets, visible `:focus-visible` outlines, `role="meter"` on HP,
labeled icon buttons, `prefers-reduced-motion` honored, no color-only
information (proficiency badges carry text), no hover-only interactions.
Mobile: bottom navigation, sheets instead of side panels, no horizontal
scroll at 375px (asserted by Playwright on every captured screen).
