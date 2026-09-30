"""create generated_documents table

Revision ID: a224f0c39678
Revises: 8e1cf3964605
Create Date: 2026-06-25 01:51:43.147230

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a224f0c39678'
down_revision: Union[str, Sequence[str], None] = '8e1cf3964605'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'generated_documents',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('hospital_id', sa.UUID(), nullable=False),
        sa.Column('patient_id', sa.UUID(), nullable=True),
        sa.Column(
            'document_type',
            sa.Enum(
                'INVOICE', 'RECEIPT', 'PRESCRIPTION', 'LAB_REPORT',
                'MEDICAL_CERTIFICATE', 'REFERRAL_LETTER', 'DISCHARGE_SUMMARY',
                name='documenttype',
            ),
            nullable=False,
        ),
        sa.Column('source_type', sa.String(length=50), nullable=False),
        sa.Column('source_id', sa.UUID(), nullable=False),
        sa.Column('storage_key', sa.String(length=500), nullable=False),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('generated_by', sa.UUID(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_generated_documents_hospital_id'),
        'generated_documents', ['hospital_id'], unique=False,
    )
    op.create_index(
        op.f('ix_generated_documents_patient_id'),
        'generated_documents', ['patient_id'], unique=False,
    )
    op.create_index(
        op.f('ix_generated_documents_document_type'),
        'generated_documents', ['document_type'], unique=False,
    )
    op.create_index(
        op.f('ix_generated_documents_source_id'),
        'generated_documents', ['source_id'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_generated_documents_source_id'), table_name='generated_documents')
    op.drop_index(op.f('ix_generated_documents_document_type'), table_name='generated_documents')
    op.drop_index(op.f('ix_generated_documents_patient_id'), table_name='generated_documents')
    op.drop_index(op.f('ix_generated_documents_hospital_id'), table_name='generated_documents')
    op.drop_table('generated_documents')