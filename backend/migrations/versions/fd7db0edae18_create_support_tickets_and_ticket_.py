"""create support_tickets and ticket_messages tables

Revision ID: fd7db0edae18
Revises: 4706029b0655
Create Date: 2026-06-22 17:04:55.449651

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fd7db0edae18'
down_revision: Union[str, Sequence[str], None] = '4706029b0655'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'support_tickets',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('hospital_id', sa.UUID(), nullable=False),
        sa.Column('opened_by', sa.UUID(), nullable=False),
        sa.Column('ticket_number', sa.String(length=20), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column(
            'category',
            sa.Enum(
                'BILLING', 'TECHNICAL', 'ACCOUNT', 'FEATURE_REQUEST',
                'BUG_REPORT', 'OTHER',
                name='ticketcategory',
            ),
            nullable=False,
        ),
        sa.Column(
            'priority',
            sa.Enum('LOW', 'NORMAL', 'HIGH', 'URGENT', name='ticketpriority'),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED', name='ticketstatus'),
            nullable=False,
        ),
        sa.Column('assigned_to', sa.UUID(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
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
        op.f('ix_support_tickets_hospital_id'),
        'support_tickets', ['hospital_id'], unique=False,
    )
    op.create_index(
        op.f('ix_support_tickets_ticket_number'),
        'support_tickets', ['ticket_number'], unique=False,
    )
    op.create_index(
        op.f('ix_support_tickets_status'),
        'support_tickets', ['status'], unique=False,
    )

    op.create_table(
        'ticket_messages',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('ticket_id', sa.UUID(), nullable=False),
        sa.Column('author_id', sa.UUID(), nullable=False),
        sa.Column(
            'author_type',
            sa.Enum('HOSPITAL_USER', 'PLATFORM_ADMIN', name='authortype'),
            nullable=False,
        ),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('is_read', sa.Boolean(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_ticket_messages_ticket_id'),
        'ticket_messages', ['ticket_id'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ticket_messages_ticket_id'), table_name='ticket_messages')
    op.drop_table('ticket_messages')
    op.drop_index(op.f('ix_support_tickets_status'), table_name='support_tickets')
    op.drop_index(op.f('ix_support_tickets_ticket_number'), table_name='support_tickets')
    op.drop_index(op.f('ix_support_tickets_hospital_id'), table_name='support_tickets')
    op.drop_table('support_tickets')