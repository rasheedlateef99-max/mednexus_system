"""create conversations, conversation_participants, and messages tables

Revision ID: b83bb4dbb890
Revises: a224f0c39678
Create Date: 2026-06-26 20:48:17.280362

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b83bb4dbb890'
down_revision: Union[str, Sequence[str], None] = 'a224f0c39678'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'conversations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('hospital_id', sa.UUID(), nullable=False),
        sa.Column(
            'conversation_type',
            sa.Enum('DIRECT', 'DEPARTMENT', 'GROUP', name='conversationtype'),
            nullable=False,
        ),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('department_id', sa.UUID(), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=False),
        sa.Column('is_archived', sa.Boolean(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column(
            'last_message_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_conversations_hospital_id'),
        'conversations', ['hospital_id'], unique=False,
    )

    op.create_table(
        'conversation_participants',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('conversation_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column(
            'joined_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column(
            'last_read_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_conversation_participants_conversation_id'),
        'conversation_participants', ['conversation_id'], unique=False,
    )
    op.create_index(
        op.f('ix_conversation_participants_user_id'),
        'conversation_participants', ['user_id'], unique=False,
    )

    op.create_table(
        'messages',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('conversation_id', sa.UUID(), nullable=False),
        sa.Column('sender_id', sa.UUID(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_messages_conversation_id'),
        'messages', ['conversation_id'], unique=False,
    )
    op.create_index(
        op.f('ix_messages_sender_id'),
        'messages', ['sender_id'], unique=False,
    )
    op.create_index(
        op.f('ix_messages_created_at'),
        'messages', ['created_at'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_messages_created_at'), table_name='messages')
    op.drop_index(op.f('ix_messages_sender_id'), table_name='messages')
    op.drop_index(op.f('ix_messages_conversation_id'), table_name='messages')
    op.drop_table('messages')
    op.drop_index(op.f('ix_conversation_participants_user_id'), table_name='conversation_participants')
    op.drop_index(op.f('ix_conversation_participants_conversation_id'), table_name='conversation_participants')
    op.drop_table('conversation_participants')
    op.drop_index(op.f('ix_conversations_hospital_id'), table_name='conversations')
    op.drop_table('conversations')