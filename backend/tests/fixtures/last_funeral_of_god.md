# Campaign: The Last Funeral of God

## Brief
สิบเจ็ดปีก่อน พระเจ้าสิ้นลม ปาฏิหาริย์กลายเป็นของหายาก และศาสนจักรควบคุมพิธีฝังศพ
ร่างทุกร่างถูกเผาก่อนอรุณรุ่ง และห้ามเอ่ยนามของเทพที่ตายแล้ว

## Central Question
ความจริงเรื่องการตายของพระเจ้าจะถูกฝังไปตลอดกาล หรือจะมีใครขุดมันขึ้นมา?

## World Facts
- ศาสนจักรผูกขาดพิธีฝังศพทั้งหมด
- ร่างผู้ตายถูกเผาก่อนพระอาทิตย์ขึ้น
- การเอ่ยนามเทพที่ตายแล้วเป็นความผิดร้ายแรง

## Location: Grey Wolf Tavern
### Type
LOCATION
### Parent
ash-quarter
### Obvious
โรงเตี๊ยมไม้เก่าคับแคบ ไฟในเตาผิงส่องแสงส้มสลัว กลิ่นเบียร์เปรี้ยวคละกับควันไม้
### Hidden
มีช่องใต้พื้นหลังถังเบียร์
### Activity
มุมหนึ่ง คนส่งสารของศาสนจักรกำลังกดดันอาลักษณ์หนุ่มด้วยเสียงกระซิบ
### Exits
- ประตูหน้า / outside / 0 -> bellmaker-street

## Location: Bellmaker Street
### Type
DISTRICT
### Parent
ash-quarter
### Obvious
ถนนหินเปียกฝน ร้านรวงปิดเงียบ ระฆังโบสถ์ดังแว่วมาจากเนินด้านบน
### Exits
- ขึ้นเนิน / up / 15 -> cathedral-district

## Location: Cathedral District
### Type
DISTRICT
### Parent
veyr
### Obvious
ลานกว้างหน้ามหาวิหารหินสีเทา ยอดหอระฆังเสียดฟ้า ผู้คนก้มหน้าเดินผ่านอย่างรีบร้อน

## Location: Ash Quarter
### Type
SETTLEMENT
### Parent
veyr
### Obvious
ย่านชนชั้นล่างของนครเวย์ริ ปล่องควันจากโรงเผาศพลอยขึ้นทุกเช้า

## Location: Veyr
### Type
REGION
### Obvious
นครหลวงเวย์ริ เมืองที่ระฆังไม่เคยหยุดตี

## Location: Black Chapel
### Type
LOCATION
### Parent
cathedral-district
### Obvious
โบสถ์เล็กหินดำ เทียนไขจุดรอบโลงศพกลางห้อง โซ่เงินพันรอบฝาโลงไว้แน่นหนา
### Exits
- ประตูหน้า / outside / 5 -> chapel-road

## Location: Chapel Road
### Type
DISTRICT
### Parent
cathedral-district
### Obvious
ถนนหินเรียบรอบมหาวิหาร ผู้แสวงบุญเดินเป็นแถวเงียบๆ ตามเสียงระฆัง

## Faction: The Last Church
### Goal
รักษาตราผนึกและความลับเรื่องการตายของพระเจ้าไว้
### Methods
ควบคุมพิธีฝังศพ ทำลายเอกสารต้องห้าม สืบสวนผู้สงสัย

## NPC: Church Courier
### Location
grey-wolf-tavern
### Voice
เย็นชา สุภาพเกินจริง
### Goal
ทำลายเอกสารฝังศพต้องห้ามชุดหนึ่ง

## NPC: Nervous Archivist
### Location
grey-wolf-tavern
### Voice
พูดเร็ว มือสั่น
### Goal
เอาตัวรอดและเก็บสำเนาเอกสารไว้หนึ่งชุด

## NPC: Mother Seraphine
### Voice
อ่อนโยนแต่หนักแน่น
### Goal
ค้นหาความจริงเรื่องระฆัง

## NPC: Mother Veyra
### Location
black-chapel
### Voice
เย็นชา หนักแน่น ไม่ยอมเผยอารมณ์
### Goal
คุ้มกันขบวนโลงศพให้ถึงที่หมายโดยไม่มีใครฝ่าฝืนกฎห้าข้อ

## NPC: Father Caldus
### Location
black-chapel
### Voice
ต่ำ ช้า พูดน้อย
### Goal
เฝ้าประตูโบสถ์และคอยเตือนกฎห้าข้อแก่ผู้มาใหม่

## NPC: Sister Nara
### Location
black-chapel
### Voice
เงียบสนิท — สื่อสารผ่านกระดานชนวนเท่านั้น
### Communication
SLATE
### Goal
จดบันทึกทุกคำพูดที่เกิดขึ้นใกล้โลงศพ

## Protocol: Coffin Escort Five Rules
### Key
coffin-escort-five-rules
### Visibility
PARTY
### Known By
- Mother Veyra
- Father Caldus
- Sister Nara
### Rules
- คุ้มกันโลงศพ
- ห้ามเปิดโลง
- ห้ามให้ใครแตะต้องโลง
- หากนักบวชตาย ให้เดินทางต่อ
- หากโลงพูด ห้ามตอบ

## Secret: God Is Alive
### Truth
พระเจ้าไม่ได้ตาย งานศพผนึกพระองค์ไว้ใต้นครหลวง
### Clues
- เอกสารชุดนั้นควรถูกเผาไปแล้ว
- ...เอกสารฝังศพ...
- ระฆังตีเองในเวลาที่ไม่มีใครสั่ง

## Secret: Purpose of Bells
### Truth
ระฆังเชื่อมกับสิ่งที่อยู่ใต้เมือง
### Clues
- เสียงระฆังทำให้สุนัขเงียบทั้งย่าน

## Threat: The Failing Seal
### Goal
ตราผนึกใต้เมืองอ่อนกำลังลง
### Next Action
รอยร้าวแรกปรากฏในสุสานหลวง
### Progress
20
### Scheduled
240

## Session 1
### Purpose
แนะนำความเสื่อมของเทพ การควบคุมพิธีฝังศพ และความผิดปกติในบันทึกของศาสนจักร
### Do Not Reveal
God is alive
### Opening Location
grey-wolf-tavern
### Present NPCs
- Church Courier
- Nervous Archivist
### Current Activity
คนส่งสารกดดันอาลักษณ์เรื่องเอกสารฝังศพเป็นการส่วนตัว
### Allowed Clues
- เอกสารชุดนั้นควรถูกเผาไปแล้ว
- ...เอกสารฝังศพ...
### Protected Secrets
- God Is Alive
- Purpose of Bells
