"""add audit columns to patients, hospital_users, appointments

Revision ID: c8fa60e9f05e
Revises: 662ceb03dff2
Create Date: 2026-07-27 14:13:10.446106

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8fa60e9f05e'
down_revision: Union[str, Sequence[str], None] = '662ceb03dff2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── patients: add updated_by, deactivated_by, deactivated_at ─────────────
    op.add_column('patients',
        sa.Column('updated_by', sa.UUID(), nullable=True)
    )
    op.add_column('patients',
        sa.Column('deactivated_by', sa.UUID(), nullable=True)
    )
    op.add_column('patients',
        sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True)
    )

    # ── hospital_users: add deactivated_by, deactivated_at ───────────────────
    op.add_column('hospital_users',
        sa.Column('deactivated_by', sa.UUID(), nullable=True)
    )
    op.add_column('hospital_users',
        sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True)
    )

    # ── appointments: add updated_by ──────────────────────────────────────────
    op.add_column('appointments',
        sa.Column('updated_by', sa.UUID(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    # ── appointments ──────────────────────────────────────────────────────────
    op.drop_column('appointments', 'updated_by')

    # ── hospital_users ────────────────────────────────────────────────────────
    op.drop_column('hospital_users', 'deactivated_at')
    op.drop_column('hospital_users', 'deactivated_by')

    # ── patients ──────────────────────────────────────────────────────────────
    op.drop_column('patients', 'deactivated_at')
    op.drop_column('patients', 'deactivated_by')
    op.drop_column('patients', 'updated_by')