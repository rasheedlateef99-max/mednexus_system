"""create announcements and announcement_reads tables

Revision ID: 69740f996368
Revises: fd7db0edae18
Create Date: 2026-06-24 02:31:38.171008

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '69740f996368'
down_revision: Union[str, Sequence[str], None] = 'fd7db0edae18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'announcements',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column(
            'severity',
            sa.Enum('INFO', 'WARNING', 'CRITICAL', name='announcementseverity'),
            nullable=False,
        ),
        sa.Column(
            'target_type',
            sa.Enum(
                'ALL_HOSPITALS', 'SPECIFIC_PLAN', 'SPECIFIC_HOSPITAL',
                name='announcementtargettype',
            ),
            nullable=False,
        ),
        sa.Column('target_value', sa.String(length=255), nullable=True),
        sa.Column('is_published', sa.Boolean(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_announcements_is_published'),
        'announcements', ['is_published'], unique=False,
    )

    op.create_table(
        'announcement_reads',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('announcement_id', sa.UUID(), nullable=False),
        sa.Column('hospital_id', sa.UUID(), nullable=False),
        sa.Column('read_by', sa.UUID(), nullable=False),
        sa.Column(
            'read_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_announcement_reads_announcement_id'),
        'announcement_reads', ['announcement_id'], unique=False,
    )
    op.create_index(
        op.f('ix_announcement_reads_hospital_id'),
        'announcement_reads', ['hospital_id'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_announcement_reads_hospital_id'), table_name='announcement_reads')
    op.drop_index(op.f('ix_announcement_reads_announcement_id'), table_name='announcement_reads')
    op.drop_table('announcement_reads')
    op.drop_index(op.f('ix_announcements_is_published'), table_name='announcements')
    op.drop_table('announcements')