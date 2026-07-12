"""Core schema baseline — every table of the pre-canon-import engine.

Reconstructed 2026-07-12: Alembic was wired in commit 292d76c but the initial
schema revision was never generated (dev environments used ``create_all``), so
the shipped ``20260710_canon`` migration had no ancestry. This root revision
creates the full base schema exactly as the models defined it BEFORE the
aliases/canon/anchors/economy/follow/npc-memory migrations, so the whole chain
upgrades an empty database to the current schema.

Revision ID: 20260708_core
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "20260708_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table('item_definitions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=True),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_item_definitions_campaign_id'), 'item_definitions', ['campaign_id'], unique=False)
    op.create_table('processed_messages',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('discord_message_id', sa.String(length=64), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=True),
    sa.Column('session_id', sa.String(length=32), nullable=True),
    sa.Column('stage', sa.String(length=16), nullable=False),
    sa.Column('category', sa.String(length=24), nullable=True),
    sa.Column('pending_action_id', sa.String(length=32), nullable=True),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_processed_messages_discord_message_id'), 'processed_messages', ['discord_message_id'], unique=True)
    op.create_table('users',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('discord_user_id', sa.String(length=64), nullable=False),
    sa.Column('display_name', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_discord_user_id'), 'users', ['discord_user_id'], unique=True)
    op.create_table('campaigns',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('discord_guild_id', sa.String(length=64), nullable=False),
    sa.Column('game_channel_id', sa.String(length=64), nullable=False),
    sa.Column('owner_user_id', sa.String(length=32), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('current_game_time', sa.Integer(), nullable=False),
    sa.Column('brief', sa.Text(), nullable=False),
    sa.Column('central_question', sa.Text(), nullable=False),
    sa.Column('session_prep', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('event_seq', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_campaigns_discord_guild_id'), 'campaigns', ['discord_guild_id'], unique=False)
    op.create_index(op.f('ix_campaigns_game_channel_id'), 'campaigns', ['game_channel_id'], unique=True)
    op.create_table('campaign_canon_records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('fact', sa.Text(), nullable=False),
    sa.Column('truth_status', sa.String(length=20), nullable=False),
    sa.Column('visibility', sa.String(length=16), nullable=False),
    sa.Column('provenance', sa.String(length=24), nullable=False),
    sa.Column('importance', sa.Integer(), nullable=False),
    sa.Column('scope_type', sa.String(length=24), nullable=True),
    sa.Column('scope_id', sa.String(length=32), nullable=True),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('campaign_members',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('active_character_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['active_character_id'], ['characters.id'], name='fk_member_active_character', ondelete='SET NULL', use_alter=True),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('campaign_id', 'user_id', name='uq_member_campaign_user')
    )
    op.create_table('combat_encounters',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('session_id', sa.String(length=32), nullable=True),
    sa.Column('scene_id', sa.String(length=32), nullable=True),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('turn_index', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('initiative_order', sa.JSON(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('events',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('session_id', sa.String(length=32), nullable=True),
    sa.Column('scene_id', sa.String(length=32), nullable=True),
    sa.Column('event_type', sa.String(length=40), nullable=False),
    sa.Column('campaign_time', sa.Integer(), nullable=False),
    sa.Column('real_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_entity', sa.String(length=80), nullable=True),
    sa.Column('target_entities', sa.JSON(), nullable=False),
    sa.Column('location_id', sa.String(length=32), nullable=True),
    sa.Column('witnesses', sa.JSON(), nullable=False),
    sa.Column('visibility', sa.String(length=16), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('mechanical_changes', sa.JSON(), nullable=False),
    sa.Column('narrative_significance', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_events_event_type'), 'events', ['event_type'], unique=False)
    op.create_index(op.f('ix_events_seq'), 'events', ['seq'], unique=False)
    op.create_table('knowledge_records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('fact', sa.Text(), nullable=False),
    sa.Column('truth_value', sa.Boolean(), nullable=False),
    sa.Column('visibility', sa.String(length=16), nullable=False),
    sa.Column('provenance', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('locations',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('description_obvious', sa.Text(), nullable=False),
    sa.Column('description_focused', sa.Text(), nullable=False),
    sa.Column('description_hidden', sa.Text(), nullable=False),
    sa.Column('connections', sa.JSON(), nullable=False),
    sa.Column('contents', sa.JSON(), nullable=False),
    sa.Column('state', sa.JSON(), nullable=False),
    sa.Column('location_type', sa.String(length=20), nullable=False),
    sa.Column('parent_id', sa.String(length=32), nullable=True),
    sa.Column('provenance', sa.String(length=20), nullable=False),
    sa.Column('weather', sa.String(length=120), nullable=False),
    sa.Column('current_activity', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_locations_parent_id'), 'locations', ['parent_id'], unique=False)
    op.create_table('npcs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('personality', sa.Text(), nullable=False),
    sa.Column('voice_register', sa.String(length=200), nullable=False),
    sa.Column('goals', sa.JSON(), nullable=False),
    sa.Column('current_location_id', sa.String(length=32), nullable=True),
    sa.Column('attitudes', sa.JSON(), nullable=False),
    sa.Column('emotional_state', sa.String(length=60), nullable=False),
    sa.Column('communication_mode', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('scheduled_world_events',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('due_game_time', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=60), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('perceivable', sa.Boolean(), nullable=False),
    sa.Column('resolved', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('secrets',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('fact', sa.Text(), nullable=False),
    sa.Column('visibility', sa.String(length=16), nullable=False),
    sa.Column('visibility_map', sa.JSON(), nullable=False),
    sa.Column('revealed', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('sessions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('active_play_state', sa.String(length=28), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attendance', sa.JSON(), nullable=False),
    sa.Column('feedback', sa.JSON(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('threats',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('goal', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('next_action', sa.Text(), nullable=False),
    sa.Column('scheduled_game_time', sa.Integer(), nullable=False),
    sa.Column('tick_amount', sa.Integer(), nullable=False),
    sa.Column('tick_interval', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('character_drafts',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('member_id', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('step', sa.Integer(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['member_id'], ['campaign_members.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('characters',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('owner_member_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('species', sa.String(length=60), nullable=False),
    sa.Column('char_class', sa.String(length=60), nullable=False),
    sa.Column('background', sa.String(length=60), nullable=False),
    sa.Column('ruleset_id', sa.String(length=16), nullable=False),
    sa.Column('str_score', sa.Integer(), nullable=False),
    sa.Column('dex_score', sa.Integer(), nullable=False),
    sa.Column('con_score', sa.Integer(), nullable=False),
    sa.Column('int_score', sa.Integer(), nullable=False),
    sa.Column('wis_score', sa.Integer(), nullable=False),
    sa.Column('cha_score', sa.Integer(), nullable=False),
    sa.Column('proficiencies', sa.JSON(), nullable=False),
    sa.Column('expertise', sa.JSON(), nullable=False),
    sa.Column('save_proficiencies', sa.JSON(), nullable=False),
    sa.Column('tool_proficiencies', sa.JSON(), nullable=False),
    sa.Column('languages', sa.JSON(), nullable=False),
    sa.Column('proficiency_bonus', sa.Integer(), nullable=False),
    sa.Column('hp', sa.Integer(), nullable=False),
    sa.Column('max_hp', sa.Integer(), nullable=False),
    sa.Column('temp_hp', sa.Integer(), nullable=False),
    sa.Column('ac', sa.Integer(), nullable=False),
    sa.Column('speed', sa.Integer(), nullable=False),
    sa.Column('hit_die', sa.Integer(), nullable=False),
    sa.Column('hit_dice_remaining', sa.Integer(), nullable=False),
    sa.Column('death_saves', sa.JSON(), nullable=False),
    sa.Column('stable', sa.Boolean(), nullable=False),
    sa.Column('dead', sa.Boolean(), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('xp', sa.Integer(), nullable=False),
    sa.Column('exhaustion', sa.Integer(), nullable=False),
    sa.Column('conditions', sa.JSON(), nullable=False),
    sa.Column('resources', sa.JSON(), nullable=False),
    sa.Column('location_id', sa.String(length=32), nullable=True),
    sa.Column('hooks', sa.JSON(), nullable=False),
    sa.Column('appearance', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_member_id'], ['campaign_members.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_characters_location_id'), 'characters', ['location_id'], unique=False)
    op.create_table('combatants',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('encounter_id', sa.String(length=32), nullable=False),
    sa.Column('entity_ref', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('initiative', sa.Integer(), nullable=False),
    sa.Column('hp', sa.Integer(), nullable=False),
    sa.Column('max_hp', sa.Integer(), nullable=False),
    sa.Column('ac', sa.Integer(), nullable=False),
    sa.Column('attack_bonus', sa.Integer(), nullable=False),
    sa.Column('damage_die', sa.Integer(), nullable=False),
    sa.Column('damage_bonus', sa.Integer(), nullable=False),
    sa.Column('is_pc', sa.Boolean(), nullable=False),
    sa.Column('alive', sa.Boolean(), nullable=False),
    sa.Column('has_action', sa.Boolean(), nullable=False),
    sa.Column('has_reaction', sa.Boolean(), nullable=False),
    sa.Column('conditions', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['encounter_id'], ['combat_encounters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('location_connections',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('from_location_id', sa.String(length=32), nullable=False),
    sa.Column('to_location_id', sa.String(length=32), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('direction', sa.String(length=40), nullable=False),
    sa.Column('travel_minutes', sa.Integer(), nullable=False),
    sa.Column('obvious', sa.Boolean(), nullable=False),
    sa.Column('one_way', sa.Boolean(), nullable=False),
    sa.Column('access_state', sa.String(length=20), nullable=False),
    sa.Column('requirement', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['from_location_id'], ['locations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_location_id'], ['locations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('npc_facts',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('npc_id', sa.String(length=32), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('fact', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=200), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['npc_id'], ['npcs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('npc_relationships',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('npc_id', sa.String(length=32), nullable=False),
    sa.Column('entity_ref', sa.String(length=80), nullable=False),
    sa.Column('attitude', sa.String(length=40), nullable=False),
    sa.Column('trust', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['npc_id'], ['npcs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('scenes',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('session_id', sa.String(length=32), nullable=False),
    sa.Column('location_id', sa.String(length=32), nullable=True),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('purpose', sa.String(length=400), nullable=False),
    sa.Column('dramatic_question', sa.String(length=400), nullable=False),
    sa.Column('tension', sa.Integer(), nullable=False),
    sa.Column('participants', sa.JSON(), nullable=False),
    sa.Column('visible_entity_ids', sa.JSON(), nullable=False),
    sa.Column('relevant_object_ids', sa.JSON(), nullable=False),
    sa.Column('immediate_threat_ids', sa.JSON(), nullable=False),
    sa.Column('pending_action_id', sa.String(length=32), nullable=True),
    sa.Column('pending_action', sa.JSON(), nullable=True),
    sa.Column('allowed_clues', sa.JSON(), nullable=False),
    sa.Column('scene_start_game_time', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('spotlight', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('active_effects',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('campaign_id', sa.String(length=32), nullable=False),
    sa.Column('character_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('spell_key', sa.String(length=80), nullable=True),
    sa.Column('requires_concentration', sa.Boolean(), nullable=False),
    sa.Column('targets', sa.JSON(), nullable=False),
    sa.Column('started_game_time', sa.Integer(), nullable=False),
    sa.Column('duration_minutes', sa.Integer(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('character_grants',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('character_id', sa.String(length=32), nullable=False),
    sa.Column('grant_type', sa.String(length=24), nullable=False),
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('name_th', sa.String(length=160), nullable=False),
    sa.Column('source_type', sa.String(length=24), nullable=False),
    sa.Column('source_key', sa.String(length=80), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('character_spells',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('character_id', sa.String(length=32), nullable=False),
    sa.Column('spell_key', sa.String(length=80), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('prepared', sa.Boolean(), nullable=False),
    sa.Column('source_type', sa.String(length=24), nullable=False),
    sa.Column('source_key', sa.String(length=80), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('character_id', 'spell_key', 'kind', name='uq_char_spell')
    )
    op.create_table('inventory_entries',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('character_id', sa.String(length=32), nullable=False),
    sa.Column('item_definition_id', sa.String(length=32), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('equipped', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_definition_id'], ['item_definitions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('resource_states',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('character_id', sa.String(length=32), nullable=False),
    sa.Column('resource_id', sa.String(length=80), nullable=False),
    sa.Column('current', sa.Integer(), nullable=False),
    sa.Column('max_value', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('character_id', 'resource_id', name='uq_char_resource')
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_characters_location_id'), table_name='characters')
    op.drop_index(op.f('ix_locations_parent_id'), table_name='locations')
    op.drop_index(op.f('ix_events_seq'), table_name='events')
    op.drop_index(op.f('ix_events_event_type'), table_name='events')
    op.drop_index(op.f('ix_campaigns_game_channel_id'), table_name='campaigns')
    op.drop_index(op.f('ix_campaigns_discord_guild_id'), table_name='campaigns')
    op.drop_index(op.f('ix_users_discord_user_id'), table_name='users')
    op.drop_index(op.f('ix_processed_messages_discord_message_id'), table_name='processed_messages')
    op.drop_index(op.f('ix_item_definitions_campaign_id'), table_name='item_definitions')
    op.drop_table('resource_states')
    op.drop_table('inventory_entries')
    op.drop_table('character_spells')
    op.drop_table('character_grants')
    op.drop_table('active_effects')
    op.drop_table('scenes')
    op.drop_table('npc_relationships')
    op.drop_table('npc_facts')
    op.drop_table('location_connections')
    op.drop_table('combatants')
    op.drop_table('characters')
    op.drop_table('character_drafts')
    op.drop_table('threats')
    op.drop_table('sessions')
    op.drop_table('secrets')
    op.drop_table('scheduled_world_events')
    op.drop_table('npcs')
    op.drop_table('locations')
    op.drop_table('knowledge_records')
    op.drop_table('events')
    op.drop_table('combat_encounters')
    op.drop_table('campaign_members')
    op.drop_table('campaign_canon_records')
    op.drop_table('campaigns')
    op.drop_table('users')
    op.drop_table('processed_messages')
    op.drop_table('item_definitions')
