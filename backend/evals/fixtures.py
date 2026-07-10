"""Representative Thai scenarios for manual narration inspection.

Each fixture is (label, task, marker-context) mirroring exactly what the engine's
context builders emit, so the eval exercises the same prompts production uses.
"""

NARRATION_FIXTURES = [
    (
        "stealth-success (window, guard unaware)",
        "generate_dm_narration",
        "SCENE: โหมด=EXPLORATION; สถานที่=โถงหน้าคฤหาสน์; เป้าหมายฉาก=เข้าไปโดยไม่ให้ยามรู้ตัว; สิ่งที่เห็น=npc:guard\n"
        "ACTION: ผมค่อยๆ เดินไปดูตรงหน้าต่าง พยายามไม่ให้ยามเห็น\n"
        "OUTCOME: success\nRESULT: stealth: 16+5=21 vs DC15 -> success",
    ),
    (
        "stealth-failure (noise, guard turns)",
        "generate_dm_narration",
        "SCENE: โหมด=EXPLORATION; สถานที่=โถงหน้าคฤหาสน์; สิ่งที่เห็น=npc:guard\n"
        "ACTION: กูย่องไปหลังลังไม้\nOUTCOME: failure\n"
        "RESULT: stealth: 3+5=8 vs DC15 -> failure\nTARGET: npc:guard",
    ),
    (
        "investigation-success (corpse, code-switched input)",
        "generate_dm_narration",
        "SCENE: โหมด=EXPLORATION; สถานที่=ตรอกหลังตลาด\n"
        "ACTION: ผมลอง inspect ศพดูว่ามีอะไรแปลกๆ\nOUTCOME: success\n"
        "RESULT: investigation: 14+3=17 vs DC15 -> success",
    ),
    (
        "auto-success (mundane door — should be brief, no drama)",
        "generate_dm_narration",
        "SCENE: โหมด=EXPLORATION; สถานที่=โรงเตี๊ยม\nACTION: ผมเดินไปเปิดประตู\n"
        "OUTCOME: success\nRESULT: resolution=AUTOMATIC_SUCCESS; outcome=success",
    ),
]

OPENING_FIXTURES = [
    (
        "session-1 opening (hooks must appear)",
        "generate_session_opening",
        "PROFILE: โทน=มืด จริงจัง; สไตล์=เน้นบทบาท\n"
        "CHARACTERS:\n"
        "- Nara (rogue): concept=โตมากับโจร ไม่ค่อยพูด ชอบโกหก; desire=อยากมีที่ของตัวเอง; "
        "fear=ถูกทิ้ง; flaw=ไว้ใจใครยาก\n"
        "- Tam (fighter): concept=ทหารเก่าที่หนีสงคราม; desire=ไถ่บาปให้เพื่อนที่ตายแทน\n"
        "LOCATION: ประตูเมืองนครฝน — กำแพงสูง ประตูไม้บานใหญ่ ปิดเร็วกว่าปกติ\nPURPOSE: -",
    ),
]

CREATION_FIXTURES = [
    (
        "creation turn 1 (concept only)",
        "guide_character_creation",
        "DRAFT_STEP: 1\nKNOWN: -\nMESSAGE: อยากเป็นผู้หญิงที่โตมากับโจร ไม่ค่อยพูด ใช้มีด แล้วชอบโกหกคน",
    ),
]

ALL = {
    "narration": NARRATION_FIXTURES,
    "opening": OPENING_FIXTURES,
    "creation": CREATION_FIXTURES,
}
