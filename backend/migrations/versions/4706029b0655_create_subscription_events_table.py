"""create subscription_events table

Revision ID: 4706029b0655
Revises: ae206b213a45
Create Date: 2026-06-22 04:50:10.935207

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4706029b0655'
down_revision: Union[str, Sequence[str], None] = 'ae206b213a45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'subscription_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('hospital_id', sa.UUID(), nullable=False),
        sa.Column('subscription_id', sa.UUID(), nullable=True),
        sa.Column(
            'event_type',
            sa.Enum(
                'PLAN_SELECTED',
                'PAYMENT_INITIALIZED',
                'PAYMENT_CONFIRMED',
                'PAYMENT_FAILED',
                'SUBSCRIPTION_ACTIVATED',
                'SUBSCRIPTION_RENEWED',
                'SUBSCRIPTION_UPGRADED',
                'SUBSCRIPTION_DOWNGRADED',
                'SUBSCRIPTION_CANCELLED',
                'SUBSCRIPTION_EXPIRED',
                'TRIAL_STARTED',
                'TRIAL_ENDED',
                name='subscriptioneventtype',
            ),
            nullable=False,
        ),
        sa.Column('plan_id', sa.UUID(), nullable=True),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=True),
        sa.Column('payment_reference', sa.String(length=255), nullable=True),
        sa.Column('provider_response', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_subscription_events_hospital_id'),
        'subscription_events', ['hospital_id'], unique=False,
    )
    op.create_index(
        op.f('ix_subscription_events_subscription_id'),
        'subscription_events', ['subscription_id'], unique=False,
    )
    op.create_index(
        op.f('ix_subscription_events_event_type'),
        'subscription_events', ['event_type'], unique=False,
    )
    op.create_index(
        op.f('ix_subscription_events_payment_reference'),
        'subscription_events', ['payment_reference'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_subscription_events_payment_reference'), table_name='subscription_events')
    op.drop_index(op.f('ix_subscription_events_event_type'), table_name='subscription_events')
    op.drop_index(op.f('ix_subscription_events_subscription_id'), table_name='subscription_events')
    op.drop_index(op.f('ix_subscription_events_hospital_id'), table_name='subscription_events')
    op.drop_table('subscription_events')