"""Premier League gameweek schema (World Cup -> LEPREM rebuild)

Drops the World Cup-era rounds/fixtures/predictions/round_entries/poll_logs tables and
recreates them for a Premier League season: Round -> Gameweek (keyed on the API's own
matchday number instead of an admin-assigned sequence), Fixture drops knockout-only
fields (is_knockout/winner/stage/group_name) and gains crest URLs, RoundEntry ->
GameweekEntry. `users` is untouched - existing accounts carry over into the new season.

This is a destructive, one-way change for the tables it touches (by design - the World
Cup's rounds/fixtures/predictions/pot history are not meant to survive the rebrand). Take
a database backup before running this against a production database with real data.

Revision ID: 7f2a9c1d4e8b
Revises: cd440f8d3dd2
Create Date: 2026-07-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7f2a9c1d4e8b'
down_revision = 'cd440f8d3dd2'
branch_labels = None
depends_on = None


def upgrade():
    # Drop in FK-safe order (children before parents). poll_logs has no FK so its
    # position doesn't matter, but it's dropped first per the plan's ordering.
    op.drop_table('poll_logs')
    op.drop_table('predictions')
    op.drop_table('round_entries')
    op.drop_table('fixtures')
    op.drop_table('rounds')

    op.create_table(
        'gameweeks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('matchday', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('stake_amount', sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('force_locked', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('matchday'),
    )
    with op.batch_alter_table('gameweeks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_gameweeks_matchday'), ['matchday'], unique=True)
        batch_op.create_index(batch_op.f('ix_gameweeks_status'), ['status'], unique=False)

    op.create_table(
        'fixtures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.Integer(), nullable=True),
        sa.Column('gameweek_id', sa.Integer(), nullable=True),
        sa.Column('home_team', sa.String(length=120), nullable=False),
        sa.Column('away_team', sa.String(length=120), nullable=False),
        sa.Column('home_short_name', sa.String(length=80), nullable=True),
        sa.Column('away_short_name', sa.String(length=80), nullable=True),
        sa.Column('home_crest_url', sa.String(length=255), nullable=True),
        sa.Column('away_crest_url', sa.String(length=255), nullable=True),
        sa.Column('kickoff_at', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('home_score', sa.Integer(), nullable=True),
        sa.Column('away_score', sa.Integer(), nullable=True),
        sa.Column('current_minute', sa.Integer(), nullable=True),
        sa.Column('current_injury_time', sa.Integer(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.Column('manually_corrected', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['gameweek_id'], ['gameweeks.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_id'),
    )
    with op.batch_alter_table('fixtures', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fixtures_external_id'), ['external_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_fixtures_gameweek_id'), ['gameweek_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fixtures_kickoff_at'), ['kickoff_at'], unique=False)

    op.create_table(
        'predictions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('predicted_home', sa.Integer(), nullable=False),
        sa.Column('predicted_away', sa.Integer(), nullable=False),
        sa.Column('points', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['fixture_id'], ['fixtures.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'fixture_id', name='uq_prediction_user_fixture'),
    )
    with op.batch_alter_table('predictions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_predictions_fixture_id'), ['fixture_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_predictions_user_id'), ['user_id'], unique=False)

    op.create_table(
        'gameweek_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('gameweek_id', sa.Integer(), nullable=False),
        sa.Column('opted_in', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['gameweek_id'], ['gameweeks.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'gameweek_id', name='uq_gameweek_entry_user_gameweek'),
    )
    with op.batch_alter_table('gameweek_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_gameweek_entries_gameweek_id'), ['gameweek_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_gameweek_entries_user_id'), ['user_id'], unique=False)

    op.create_table(
        'poll_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_at', sa.DateTime(), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('succeeded', sa.Boolean(), nullable=False),
        sa.Column('fixtures_created', sa.Integer(), nullable=False),
        sa.Column('fixtures_updated', sa.Integer(), nullable=False),
        sa.Column('fixtures_scored', sa.Integer(), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('poll_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_poll_logs_run_at'), ['run_at'], unique=False)


def downgrade():
    # Recreates the World Cup-era table shapes. Data (2026/27 fixtures/predictions/pots)
    # is not preserved across this round trip - only the structure is restored.
    op.drop_table('poll_logs')
    op.drop_table('gameweek_entries')
    op.drop_table('predictions')
    op.drop_table('fixtures')
    op.drop_table('gameweeks')

    op.create_table(
        'rounds',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('stake_amount', sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('force_locked', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sequence'),
    )
    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rounds_status'), ['status'], unique=False)

    op.create_table(
        'fixtures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.Integer(), nullable=True),
        sa.Column('round_id', sa.Integer(), nullable=True),
        sa.Column('home_team', sa.String(length=120), nullable=False),
        sa.Column('away_team', sa.String(length=120), nullable=False),
        sa.Column('home_short_name', sa.String(length=80), nullable=True),
        sa.Column('away_short_name', sa.String(length=80), nullable=True),
        sa.Column('stage', sa.String(length=64), nullable=True),
        sa.Column('group_name', sa.String(length=64), nullable=True),
        sa.Column('kickoff_at', sa.DateTime(), nullable=False),
        sa.Column('is_knockout', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('home_score_90', sa.Integer(), nullable=True),
        sa.Column('away_score_90', sa.Integer(), nullable=True),
        sa.Column('current_minute', sa.Integer(), nullable=True),
        sa.Column('current_injury_time', sa.Integer(), nullable=True),
        sa.Column('winner', sa.String(length=8), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.Column('manually_corrected', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['round_id'], ['rounds.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_id'),
    )
    with op.batch_alter_table('fixtures', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fixtures_external_id'), ['external_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_fixtures_round_id'), ['round_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fixtures_kickoff_at'), ['kickoff_at'], unique=False)

    op.create_table(
        'predictions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('predicted_home', sa.Integer(), nullable=False),
        sa.Column('predicted_away', sa.Integer(), nullable=False),
        sa.Column('points', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['fixture_id'], ['fixtures.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'fixture_id', name='uq_prediction_user_fixture'),
    )
    with op.batch_alter_table('predictions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_predictions_fixture_id'), ['fixture_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_predictions_user_id'), ['user_id'], unique=False)

    op.create_table(
        'round_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('round_id', sa.Integer(), nullable=False),
        sa.Column('opted_in', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['round_id'], ['rounds.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'round_id', name='uq_round_entry_user_round'),
    )
    with op.batch_alter_table('round_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_round_entries_round_id'), ['round_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_round_entries_user_id'), ['user_id'], unique=False)

    op.create_table(
        'poll_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_at', sa.DateTime(), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('succeeded', sa.Boolean(), nullable=False),
        sa.Column('fixtures_created', sa.Integer(), nullable=False),
        sa.Column('fixtures_updated', sa.Integer(), nullable=False),
        sa.Column('fixtures_scored', sa.Integer(), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('poll_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_poll_logs_run_at'), ['run_at'], unique=False)
