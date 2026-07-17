"""Phase 1 faith content, resolver, campaign isolation, and validation."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app.db.session import Database
from app.rules_content.faith_registry import (
    DeityRelationship,
    DeityResolutionStatus,
    DeityResolver,
    FaithContentError,
    FaithRegistry,
    parse_pantheon_markdown,
)
from app.services.campaigns import CampaignService
from app.services.faith import FaithService
from tests.support.factories import build_world

CONTENT_ROOT = Path(__file__).resolve().parents[1] / "app" / "rules_content" / "pantheons"
PACK_DIR = CONTENT_ROOT / "forgotten_realms_v1"
SOURCE_FILE = PACK_DIR / "Forgotten_Realms_Pantheon_Detailed.md"

EXPECTED_DEITIES = (
    "Ao",
    "Mystra",
    "Lathander",
    "Selûne",
    "Shar",
    "Bane",
    "Bhaal",
    "Myrkul",
    "Kelemvor",
    "Tyr",
    "Torm",
    "Oghma",
    "Tymora",
    "Beshaba",
    "Silvanus",
    "Auril",
    "Azuth",
    "Chauntea",
    "Cyric",
    "Deneir",
    "Eldath",
    "Gond",
    "Helm",
    "Ilmater",
    "Leira",
    "Lliira",
    "Loviatar",
    "Malar",
    "Mask",
    "Mielikki",
    "Milil",
    "Savras",
    "Sune",
    "Talona",
    "Talos",
    "Tempus",
    "Umberlee",
    "Waukeen",
)


def _copied_content(tmp_path: Path) -> Path:
    root = tmp_path / "pantheons"
    shutil.copytree(PACK_DIR, root / "forgotten_realms_v1")
    return root


def _json(root: Path, name: str) -> tuple[Path, object]:
    path = root / "forgotten_realms_v1" / name
    return path, json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _refresh_source_hash(root: Path) -> None:
    manifest_path, manifest = _json(root, "manifest.json")
    source = root / "forgotten_realms_v1" / manifest["source_file"]
    normalized = source.read_text(encoding="utf-8").rstrip("\r\n").replace("\r\n", "\n")
    manifest["source_sha256"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    _write_json(manifest_path, manifest)


def _add_test_pantheon(root: Path) -> None:
    """A tiny non-FR fixture proving campaigns may activate different packs."""
    pack = root / "test_pantheon_v1"
    pack.mkdir()
    source = (
        "# Test Pantheon\n\n"
        "### ✨ Test Deity (เทพทดสอบ) - เทพสำหรับทดสอบการแยกแคมเปญ\n"
        "*   **ฝักใฝ่ (Alignment):** เป็นกลางโดยสมบูรณ์ (True Neutral)\n"
        "*   **เขตแดนศักดิ์สิทธิ์ (Domains):** Knowledge\n"
        "*   **เรื่องเล่าและตำนาน:** ข้อมูลทดสอบเท่านั้น"
    )
    (pack / "source.md").write_text(source, encoding="utf-8")
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    _write_json(pack / "manifest.json", {
        "key": "test_pantheon",
        "display_name_en": "Test Pantheon",
        "display_name_th": "ทำเนียบทดสอบ",
        "setting": "Test Setting",
        "content_pack_id": "test_pantheon_v1",
        "version": 1,
        "deity_keys": ["test_deity"],
        "activation_status": "AVAILABLE",
        "source_file": "source.md",
        "source_sha256": source_hash,
        "source_reference": "test fixture",
        "provenance": "TEST_FIXTURE",
        "content_status": "TEST_ONLY",
    })
    _write_json(pack / "deities.json", [{
        "key": "test_deity",
        "pantheon_key": "test_pantheon",
        "content_pack_id": "test_pantheon_v1",
        "source_name_en": "Test Deity",
        "cleric_capable": True,
        "selectable_as_belief": True,
        "provenance": "TEST_FIXTURE",
        "implementation_status": "TEST_ONLY",
    }])


def test_complete_owner_markdown_parses_and_every_deity_loads():
    source = SOURCE_FILE.read_text(encoding="utf-8")
    parsed = parse_pantheon_markdown(source)
    registry = FaithRegistry()

    assert tuple(entry.canonical_name_en for entry in parsed) == EXPECTED_DEITIES
    assert tuple(deity.canonical_name_en for deity in registry.deities.values()) == EXPECTED_DEITIES
    assert len(registry.deities) == 38
    assert "แดนพิพากษาอันเที่ยงธรรมและไร้อคติ" in (
        registry.deities["kelemvor"].full_owner_provided_lore
    )
    assert registry.pantheons["forgotten_realms"].deity_keys == tuple(registry.deities)
    assert registry.warnings  # optional absent fields warn; they do not block loading
    normalized = source.rstrip("\r\n").replace("\r\n", "\n")
    assert hashlib.sha256(normalized.encode("utf-8")).hexdigest() == (
        "fb7dcf7e428ab266743d95e9e75dc35bafd697b3b2fccc012d9e0c5f359992d8"
    )


@pytest.mark.parametrize("name", EXPECTED_DEITIES)
def test_every_english_name_resolves_exactly(name: str):
    result = FaithRegistry().resolver().resolve(name)
    assert result.status is DeityResolutionStatus.EXACT
    assert result.deity_key


@pytest.mark.parametrize(
    ("name_th", "key"),
    [
        ("เอโอ", "ao"),
        ("มิสตรา", "mystra"),
        ("ลาธานเดอร์", "lathander"),
        ("เซลูเน่", "selune"),
        ("ชาร์", "shar"),
        ("เบน", "bane"),
        ("บาล", "bhaal"),
        ("เมอร์คูล", "myrkul"),
        ("เคเลมวอร์", "kelemvor"),
        ("ทีร์", "tyr"),
        ("ทอร์ม", "torm"),
        ("อ็อกมา", "oghma"),
        ("ไทโมรา", "tymora"),
        ("เบชาบา", "beshaba"),
        ("ซิลวานัส", "silvanus"),
    ],
)
def test_every_thai_name_resolves_exactly(name_th: str, key: str):
    result = FaithRegistry().resolver().resolve(name_th)
    assert result.status is DeityResolutionStatus.EXACT
    assert result.deity_key == key


def test_alias_title_and_normalized_reference_resolution():
    resolver = FaithRegistry().resolver()

    assert resolver.resolve("Midnight").deity_key == "mystra"
    assert resolver.resolve("The Morninglord").deity_key == "lathander"
    normalized = resolver.resolve("  THE---MORNINGLORD  ")
    assert normalized.status is DeityResolutionStatus.NORMALIZED_UNIQUE
    assert normalized.deity_key == "lathander"
    assert resolver.resolve("not a supplied deity").status is DeityResolutionStatus.NOT_FOUND

    apostrophe = FaithRegistry().deities["ao"].model_copy(
        update={"aliases": ("Keeper's Measure",)}
    )
    apostrophe_result = DeityResolver((apostrophe,)).resolve("keeper’s-measure")
    assert apostrophe_result.status is DeityResolutionStatus.NORMALIZED_UNIQUE
    assert apostrophe_result.deity_key == "ao"


def test_ambiguous_reference_is_never_silently_selected():
    registry = FaithRegistry()
    first = registry.deities["ao"].model_copy(update={"aliases": ("shared-name",)})
    second = registry.deities["mystra"].model_copy(update={"aliases": ("shared name",)})

    result = DeityResolver((first, second)).resolve("SHARED_name")

    assert result.status is DeityResolutionStatus.AMBIGUOUS
    assert result.deity_key is None
    assert result.candidate_keys == ("ao", "mystra")


@pytest.mark.asyncio
async def test_activation_cleric_rules_and_defined_relationships(db):
    world = await build_world(db)
    async with db.unit_of_work() as session:
        faith = FaithService(session)
        assert await faith.list_active_pantheons(world.campaign_id) == []
        assert await faith.list_selectable_deities(world.campaign_id) == []

        await faith.activate_pantheon(world.campaign_id, "forgotten_realms")
        active = await faith.list_active_pantheons(world.campaign_id)
        selectable = await faith.list_selectable_deities(world.campaign_id)
        cleric_deities = await faith.list_cleric_compatible_deities(world.campaign_id)

        assert [pantheon.key for pantheon in active] == ["forgotten_realms"]
        assert len(selectable) == 38
        assert any(deity.key == "ao" for deity in selectable)
        assert not await faith.grants_cleric_powers(world.campaign_id, "ao")
        assert all(deity.domains for deity in cleric_deities)
        assert "ao" not in {deity.key for deity in cleric_deities}
        assert await faith.list_deity_domains(world.campaign_id, "selune") == (
            "Knowledge", "Life", "Twilight"
        )
        assert await faith.defined_relationship(
            world.campaign_id, "selune", "shar"
        ) is DeityRelationship.RIVAL
        assert [deity.key for deity in await faith.list_rivals(
            world.campaign_id, "selune"
        )] == ["shar"]
        assert [deity.key for deity in await faith.list_allies(
            world.campaign_id, "tyr"
        )] == ["torm"]


@pytest.mark.asyncio
async def test_campaign_activation_is_isolated_and_inactive_content_unavailable(db):
    world = await build_world(db)
    async with db.unit_of_work() as session:
        campaigns = CampaignService(session)
        other = await campaigns.create_campaign(
            name="Other World",
            discord_guild_id="guild-2",
            game_channel_id="chan-2",
            owner_discord_user_id="owner-2",
            owner_display_name="Other DM",
        )
        faith = FaithService(session)
        await faith.activate_pantheon(world.campaign_id, "forgotten_realms")

        assert await faith.get_deity(world.campaign_id, "mystra") is not None
        assert await faith.get_deity(other.id, "mystra") is None
        inactive_resolution = await faith.resolve_deity_reference(other.id, "Mystra")
        assert inactive_resolution.status is DeityResolutionStatus.NOT_FOUND
        assert await faith.list_selectable_deities(other.id) == []

        await faith.activate_pantheon(other.id, "forgotten_realms")
        assert len(await faith.list_selectable_deities(other.id)) == 38
        await faith.deactivate_pantheon(other.id, "forgotten_realms")
        assert await faith.list_selectable_deities(other.id) == []
        assert len(await faith.list_selectable_deities(world.campaign_id)) == 38


@pytest.mark.asyncio
async def test_different_campaigns_may_activate_different_pantheons(db, tmp_path):
    root = _copied_content(tmp_path)
    _add_test_pantheon(root)
    registry = FaithRegistry(root)
    world = await build_world(db)
    async with db.unit_of_work() as session:
        other = await CampaignService(session).create_campaign(
            name="Other Pantheon",
            discord_guild_id="guild-other",
            game_channel_id="chan-other",
            owner_discord_user_id="owner-other",
            owner_display_name="Other DM",
        )
        faith = FaithService(session, registry)
        await faith.activate_pantheon(world.campaign_id, "forgotten_realms")
        await faith.activate_pantheon(other.id, "test_pantheon")

        assert [p.key for p in await faith.list_active_pantheons(world.campaign_id)] == [
            "forgotten_realms"
        ]
        assert [p.key for p in await faith.list_active_pantheons(other.id)] == [
            "test_pantheon"
        ]
        assert await faith.get_deity(world.campaign_id, "test_deity") is None
        assert await faith.get_deity(other.id, "mystra") is None


@pytest.mark.asyncio
async def test_registry_and_campaign_activation_reload_after_restart(tmp_path):
    path = (tmp_path / "faith-restart.sqlite3").as_posix()
    url = f"sqlite+aiosqlite:///{path}"
    first = Database(url)
    await first.create_all()
    try:
        async with first.unit_of_work() as session:
            campaign = await CampaignService(session).create_campaign(
                name="Persistent Faith",
                discord_guild_id="g",
                game_channel_id="c",
                owner_discord_user_id="owner",
                owner_display_name="DM",
            )
            campaign_id = campaign.id
            await FaithService(session, FaithRegistry()).activate_pantheon(
                campaign_id, "forgotten_realms"
            )
    finally:
        await first.dispose()

    restarted = Database(url)
    try:
        fresh_registry = FaithRegistry()
        async with restarted.session() as session:
            faith = FaithService(session, fresh_registry)
            assert [pantheon.key for pantheon in await faith.list_active_pantheons(
                campaign_id
            )] == ["forgotten_realms"]
            assert (await faith.resolve_deity_reference(
                campaign_id, "มิสตรา"
            )).deity_key == "mystra"
    finally:
        await restarted.dispose()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_key", "duplicate deity keys"),
        ("duplicate_alias", "duplicate deity alias"),
        ("missing_name", "missing canonical deity name"),
        ("unknown_pantheon", "unknown pantheon key"),
        ("invalid_alignment", "invalid alignment"),
        ("invalid_domain", "invalid domains"),
        ("unknown_rival", "unknown rival references"),
        ("unknown_ally", "unknown ally references"),
        ("self_rival", "self-rival"),
        ("self_ally", "self-ally"),
        ("cleric_without_domain", "must have at least one valid domain"),
        ("missing_provenance", "invalid metadata"),
    ],
)
def test_invalid_content_fails_closed(tmp_path: Path, mutation: str, message: str):
    root = _copied_content(tmp_path)
    deity_path, deities = _json(root, "deities.json")
    source_path = root / "forgotten_realms_v1" / SOURCE_FILE.name

    if mutation == "duplicate_key":
        deities.append(dict(deities[0]))
        _write_json(deity_path, deities)
    elif mutation == "duplicate_alias":
        deities[0]["aliases"] = ["shared-faith"]
        deities[1]["aliases"] = ["shared faith"]
        _write_json(deity_path, deities)
    elif mutation == "missing_name":
        text = source_path.read_text(encoding="utf-8")
        source_path.write_text(text.replace("🌌 Ao (เอโอ)", "🌌 (เอโอ)"), encoding="utf-8")
        _refresh_source_hash(root)
    elif mutation == "unknown_pantheon":
        deities[0]["pantheon_key"] = "missing_pantheon"
        _write_json(deity_path, deities)
    elif mutation == "invalid_alignment":
        text = source_path.read_text(encoding="utf-8")
        source_path.write_text(text.replace("(True Neutral)", "(Unruly Helpful)", 1), encoding="utf-8")
        _refresh_source_hash(root)
    elif mutation == "invalid_domain":
        text = source_path.read_text(encoding="utf-8")
        source_path.write_text(text.replace("Knowledge, Arcana", "Knowledge, Lasers", 1), encoding="utf-8")
        _refresh_source_hash(root)
    elif mutation == "unknown_rival":
        deities[0]["rivals"] = ["missing_deity"]
        _write_json(deity_path, deities)
    elif mutation == "unknown_ally":
        deities[0]["allies"] = ["missing_deity"]
        _write_json(deity_path, deities)
    elif mutation == "self_rival":
        deities[0]["rivals"] = ["ao"]
        _write_json(deity_path, deities)
    elif mutation == "self_ally":
        deities[0]["allies"] = ["ao"]
        _write_json(deity_path, deities)
    elif mutation == "cleric_without_domain":
        deities[0]["cleric_capable"] = True
        _write_json(deity_path, deities)
    elif mutation == "missing_provenance":
        deities[0].pop("provenance")
        _write_json(deity_path, deities)

    with pytest.raises(FaithContentError, match=message):
        FaithRegistry(root)


def test_duplicate_content_pack_key_fails(tmp_path: Path):
    root = _copied_content(tmp_path)
    shutil.copytree(root / "forgotten_realms_v1", root / "duplicate_pack")

    with pytest.raises(FaithContentError, match="duplicate content-pack key"):
        FaithRegistry(root)


@pytest.mark.asyncio
async def test_campaign_activating_missing_pantheon_fails_validation(db):
    world = await build_world(db)
    async with db.unit_of_work() as session:
        campaign = await CampaignService(session).get_campaign(world.campaign_id)
        campaign.active_pantheon_keys = ["missing_pantheon"]
        with pytest.raises(FaithContentError, match="activates missing pantheon"):
            await FaithService(session).validate_campaign_activations(world.campaign_id)
