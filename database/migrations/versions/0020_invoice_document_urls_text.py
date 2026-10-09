"""electronic invoices: pdf_url/xml_url as TEXT (Alegra returns signed S3 URLs far longer than 500 chars)"""
from alembic import op
import sqlalchemy as sa


revision = "0020"
down_revision = "0019"


def upgrade():
    op.alter_column("electronic_invoices", "pdf_url", type_=sa.Text(), existing_type=sa.String(500), existing_nullable=True)
    op.alter_column("electronic_invoices", "xml_url", type_=sa.Text(), existing_type=sa.String(500), existing_nullable=True)


def downgrade():
    op.alter_column("electronic_invoices", "pdf_url", type_=sa.String(500), existing_type=sa.Text(),
                    postgresql_using="left(pdf_url, 500)")
    op.alter_column("electronic_invoices", "xml_url", type_=sa.String(500), existing_type=sa.Text(),
                    postgresql_using="left(xml_url, 500)")
