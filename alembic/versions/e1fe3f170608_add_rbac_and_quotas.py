"""Add RBAC and Quotas

Revision ID: e1fe3f170608
Revises: 829ed3b4c6ff
Create Date: 2026-02-25 20:12:03.350490

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1fe3f170608'
down_revision: Union[str, Sequence[str], None] = '829ed3b4c6ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema with safe data migrations."""
    # 1. Create tables
    op.create_table(
        'roles',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(length=50), nullable=False)
    )
    op.create_index(op.f('ix_roles_name'), 'roles', ['name'], unique=True)
    
    op.create_table(
        'user_quotas',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('max_containers', sa.Integer(), server_default='3', nullable=False),
        sa.Column('active_containers', sa.Integer(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE')
    )
    op.create_index(op.f('ix_user_quotas_user_id'), 'user_quotas', ['user_id'], unique=True)

    # 2. Add role_id to users (nullable initially)
    op.add_column('users', sa.Column('role_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_users_role_id_roles', 'users', 'roles', ['role_id'], ['id'], ondelete='RESTRICT')
    op.create_index(op.f('ix_users_role_id'), 'users', ['role_id'], unique=False)

    # 3. Data Migration
    import uuid
    admin_role_id = str(uuid.uuid4())
    developer_role_id = str(uuid.uuid4())
    viewer_role_id = str(uuid.uuid4())
    
    op.execute(f"INSERT INTO roles (id, name) VALUES ('{admin_role_id}', 'admin'), ('{developer_role_id}', 'developer'), ('{viewer_role_id}', 'viewer')")
    
    # Map old boolean flag to new role entities safely
    op.execute(f"UPDATE users SET role_id = '{admin_role_id}' WHERE is_admin = true")
    op.execute(f"UPDATE users SET role_id = '{developer_role_id}' WHERE is_admin = false OR is_admin IS NULL")
    
    # Pre-provision quota slots for existing users leveraging PostgreSQL's gen_random_uuid()
    op.execute("INSERT INTO user_quotas (id, user_id, max_containers, active_containers) SELECT gen_random_uuid(), id, 3, 0 FROM users")
    
    # 4. Enforce non-nullable constraint on role_id after data migration
    op.alter_column('users', 'role_id', existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=False)

    # 5. Drop old structure
    op.drop_column('users', 'is_admin')

def downgrade() -> None:
    """Downgrade schema."""
    # 1. Restore is_admin flag
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), server_default='false', nullable=False))
    
    # 2. Fallback Data Migration
    op.execute("""
        UPDATE users SET is_admin = true 
        FROM roles WHERE users.role_id = roles.id AND roles.name = 'admin'
    """)
    
    # 3. Demolish RBAC system
    op.drop_constraint('fk_users_role_id_roles', 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_role_id'), table_name='users')
    op.drop_column('users', 'role_id')
    
    op.drop_index(op.f('ix_user_quotas_user_id'), table_name='user_quotas')
    op.drop_table('user_quotas')
    
    op.drop_index(op.f('ix_roles_name'), table_name='roles')
    op.drop_table('roles')
