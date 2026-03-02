"""multi_container_deployments

Revision ID: 2e17cdd8f496
Revises: e1fe3f170608
Create Date: 2026-03-02 16:15:42.338826

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '2e17cdd8f496'
down_revision: Union[str, Sequence[str], None] = 'e1fe3f170608'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create new ENUM for container status safely
    container_status_enum = postgresql.ENUM('PENDING', 'RUNNING', 'EXITED', 'FAILED', name='container_status_enum')
    
    # Check if enum exists first to avoid asyncpg exception
    conn = op.get_bind()
    res = conn.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'container_status_enum'")).scalar()
    if not res:
        container_status_enum.create(conn)
    
    # We redefine it with create_type=False for the column, so create_table doesn't try to create it again.
    container_status_enum_col = postgresql.ENUM('PENDING', 'RUNNING', 'EXITED', 'FAILED', name='container_status_enum', create_type=False)

    # Clean existing deployments to avoid constraint conflicts during migration
    op.execute("TRUNCATE TABLE deployments CASCADE")

    # 2. Modify deployments table
    # Drop old unsupported columns
    op.drop_column('deployments', 'blueprint_id')
    op.drop_column('deployments', 'project_id')
    op.drop_column('deployments', 'image_tag')
    op.drop_column('deployments', 'env_vars')
    op.drop_column('deployments', 'cpu_limit')
    op.drop_column('deployments', 'container_id')
    op.drop_column('deployments', 'internal_port')
    op.drop_column('deployments', 'external_port')

    # Add new columns (user_id and network_name)
    op.add_column('deployments', sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False))
    op.add_column('deployments', sa.Column('network_name', sa.String(length=255), nullable=True))
    
    # Create constraints, indices, and Foreign Key for deployments
    op.create_unique_constraint('uq_deployments_network_name', 'deployments', ['network_name'])
    op.create_index(op.f('ix_deployments_network_name'), 'deployments', ['network_name'], unique=True)
    op.create_index(op.f('ix_deployments_user_id'), 'deployments', ['user_id'], unique=False)
    op.create_foreign_key('fk_deployments_user_id_users', 'deployments', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # 3. Create new deployment_containers table
    op.create_table('deployment_containers',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('deployment_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('container_id', sa.String(length=255), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('image', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False),
        sa.Column('status', container_status_enum_col, server_default='PENDING', nullable=False),
        
        sa.ForeignKeyConstraint(['deployment_id'], ['deployments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Indices for container queries
    op.create_index(op.f('ix_deployment_containers_container_id'), 'deployment_containers', ['container_id'], unique=False)
    op.create_index(op.f('ix_deployment_containers_deployment_id'), 'deployment_containers', ['deployment_id'], unique=False)
    op.create_index(op.f('ix_deployment_containers_status'), 'deployment_containers', ['status'], unique=False)


def downgrade() -> None:
    # Drop new table
    op.drop_table('deployment_containers')
    
    # Drop new ENUM
    container_status_enum = postgresql.ENUM('PENDING', 'RUNNING', 'EXITED', 'FAILED', name='container_status_enum')
    container_status_enum.drop(op.get_bind(), checkfirst=True)
    
    # Rollback changes to deployments table
    op.drop_constraint('fk_deployments_user_id_users', 'deployments', type_='foreignkey')
    op.drop_index(op.f('ix_deployments_user_id'), table_name='deployments')
    op.drop_index(op.f('ix_deployments_network_name'), table_name='deployments')
    op.drop_constraint('uq_deployments_network_name', 'deployments', type_='unique')
    
    op.drop_column('deployments', 'network_name')
    op.drop_column('deployments', 'user_id')
    
    # Restore old columns to deployments
    op.add_column('deployments', sa.Column('blueprint_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('deployments', sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('deployments', sa.Column('image_tag', sa.String(length=255), nullable=True))
    op.add_column('deployments', sa.Column('env_vars', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=True))
    op.add_column('deployments', sa.Column('cpu_limit', sa.String(length=50), nullable=True))
    op.add_column('deployments', sa.Column('container_id', sa.String(length=255), nullable=True))
    op.add_column('deployments', sa.Column('internal_port', sa.Integer(), nullable=True))
    op.add_column('deployments', sa.Column('external_port', sa.Integer(), nullable=True))
    
    op.create_foreign_key('fk_deployments_blueprint_id_blueprints', 'deployments', 'blueprints', ['blueprint_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_deployments_project_id_projects', 'deployments', 'projects', ['project_id'], ['id'], ondelete='CASCADE')
