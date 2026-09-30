"""create notifications table

Revision ID: 662ceb03dff2
Revises: b83bb4dbb890
Create Date: 2026-07-05 18:52:58.662143

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '662ceb03dff2'
down_revision: Union[str, Sequence[str], None] = 'b83bb4dbb890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'notifications',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('hospital_id', sa.UUID(), nullable=False),
        sa.Column('recipient_id', sa.UUID(), nullable=False),
        sa.Column(
            'event_type',
            sa.Enum(
                'CRITICAL_LAB_RESULT',
                'SUPPORT_TICKET_REPLY',
                'SUPPORT_TICKET_RESOLVED',
                'SUPPORT_TICKET_CLOSED',
                'ANNOUNCEMENT_PUBLISHED',
                'PRESCRIPTION_DISPENSED',
                name='notificationeventtype',
            ),
            nullable=False,
        ),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('link_path', sa.String(length=500), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=False),
        sa.Column('source_type', sa.String(length=50), nullable=True),
        sa.Column('source_id', sa.UUID(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_notifications_hospital_id'),
        'notifications', ['hospital_id'], unique=False,
    )
    op.create_index(
        op.f('ix_notifications_recipient_id'),
        'notifications', ['recipient_id'], unique=False,
    )
    op.create_index(
        op.f('ix_notifications_event_type'),
        'notifications', ['event_type'], unique=False,
    )
    op.create_index(
        op.f('ix_notifications_is_read'),
        'notifications', ['is_read'], unique=False,
    )
    op.create_index(
        op.f('ix_notifications_created_at'),
        'notifications', ['created_at'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_notifications_created_at'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_is_read'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_event_type'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_recipient_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_hospital_id'), table_name='notifications')
    op.drop_table('notifications')