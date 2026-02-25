"""initial_schema

Revision ID: 829ed3b4c6ff
Revises: 
Create Date: 2026-02-25 16:15:23.691721

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '829ed3b4c6ff'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Declare enums with create_type=False so SQLAlchemy never auto-creates them.
# We create/drop them manually via op.execute().
deployment_status = postgresql.ENUM(
    'PENDING', 'RUNNING', 'STOPPED', 'FAILED',
    name='deployment_status_enum',
    create_type=False,
)
audited_entity_type = postgresql.ENUM(
    'USER', 'PROJECT', 'BLUEPRINT', 'DEPLOYMENT',
    name='audited_entity_type_enum',
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    # Create PostgreSQL enum types before the tables that reference them.
    op.execute("CREATE TYPE audited_entity_type_enum AS ENUM ('USER', 'PROJECT', 'BLUEPRINT', 'DEPLOYMENT')")
    op.execute("CREATE TYPE deployment_status_enum AS ENUM ('PENDING', 'RUNNING', 'STOPPED', 'FAILED')")

    op.create_table('blueprints',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('image_tag', sa.String(length=255), nullable=False),
        sa.Column('default_port', sa.Integer(), nullable=False),
        sa.Column('default_env_vars', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('cpu_limit', sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_blueprints_name'), 'blueprints', ['name'], unique=True)

    op.create_table('users',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('is_admin', sa.Boolean(), server_default='false', nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)

    op.create_table('audit_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('entity_type', audited_entity_type, nullable=False),
        sa.Column('entity_id', sa.String(length=255), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_entity_type'), 'audit_logs', ['entity_type'], unique=False)
    op.create_index(op.f('ix_audit_logs_timestamp'), 'audit_logs', ['timestamp'], unique=False)
    op.create_index(op.f('ix_audit_logs_user_id'), 'audit_logs', ['user_id'], unique=False)

    op.create_table('projects',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_id', 'name', name='uq_owner_project_name'),
    )
    op.create_index(op.f('ix_projects_name'), 'projects', ['name'], unique=False)
    op.create_index(op.f('ix_projects_owner_id'), 'projects', ['owner_id'], unique=False)

    op.create_table('deployments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('image_tag', sa.String(length=255), nullable=False),
        sa.Column('env_vars', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('cpu_limit', sa.String(length=50), nullable=True),
        sa.Column('status', deployment_status, server_default='PENDING', nullable=False),
        sa.Column('container_id', sa.String(length=255), nullable=True),
        sa.Column('internal_port', sa.Integer(), nullable=False),
        sa.Column('external_port', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('blueprint_id', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['blueprint_id'], ['blueprints.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_port'),
    )
    op.create_index(op.f('ix_deployments_blueprint_id'), 'deployments', ['blueprint_id'], unique=False)
    op.create_index(op.f('ix_deployments_container_id'), 'deployments', ['container_id'], unique=False)
    op.create_index(op.f('ix_deployments_project_id'), 'deployments', ['project_id'], unique=False)
    op.create_index(op.f('ix_deployments_status'), 'deployments', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_deployments_status'), table_name='deployments')
    op.drop_index(op.f('ix_deployments_project_id'), table_name='deployments')
    op.drop_index(op.f('ix_deployments_container_id'), table_name='deployments')
    op.drop_index(op.f('ix_deployments_blueprint_id'), table_name='deployments')
    op.drop_table('deployments')
    op.drop_index(op.f('ix_projects_owner_id'), table_name='projects')
    op.drop_index(op.f('ix_projects_name'), table_name='projects')
    op.drop_table('projects')
    op.drop_index(op.f('ix_audit_logs_user_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_timestamp'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_entity_type'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_blueprints_name'), table_name='blueprints')
    op.drop_table('blueprints')
    op.execute("DROP TYPE IF EXISTS deployment_status_enum")
    op.execute("DROP TYPE IF EXISTS audited_entity_type_enum")
