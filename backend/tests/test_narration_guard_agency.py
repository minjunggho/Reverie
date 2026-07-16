"""Decision prompts must preserve player agency without delegating DM-owned agency."""

from app.ai.narration_guard import (
    is_dm_agency_question,
    is_invalid_decision_question,
    screen_decision_prompt,
    screen_narration,
)


def test_exact_playtest_npc_agency_prompt_is_rewritten():
    prompt = "Oruktyr จะเสนอแนะแนววิธีใดให้เนเนะโกะ?"

    assert is_dm_agency_question(prompt, "เนเนะโกะ")
    assert screen_decision_prompt(prompt, "เนเนะโกะ") == "เนเนะโกะจะทำอย่างไร?"


def test_other_npc_and_enemy_agency_questions_are_rewritten():
    assert is_dm_agency_question("ยามจะตอบอะไร?", "Veskan")
    assert is_dm_agency_question("How will the monster react?", "Veskan")
    assert is_invalid_decision_question("What will Oruktyr suggest?", "Veskan")


def test_actor_owned_decision_questions_remain_allowed():
    assert not is_dm_agency_question("เนเนะโกะจะทำอย่างไร?", "เนเนะโกะ")
    assert not is_dm_agency_question("Veskan จะพูดอะไรกับยาม?", "Veskan")
    assert screen_decision_prompt("Veskan จะทำอย่างไร?", "Veskan") == "Veskan จะทำอย่างไร?"


def test_invalid_question_is_removed_from_narration_but_scene_is_preserved():
    text = (
        "Oruktyr หยุดคิด สายตายังจับอยู่ที่ประตูมืด\n"
        "Oruktyr จะเสนอแนะแนววิธีใดให้เนเนะโกะ?"
    )

    screened, changed = screen_narration(text, "เนเนะโกะ")

    assert changed
    assert "หยุดคิด" in screened
    assert "เสนอแนะแนววิธีใด" not in screened
