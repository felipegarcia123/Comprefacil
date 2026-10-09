"""allow long inventory movement type names such as INITIAL_STOCK"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"


def upgrade():
    op.alter_column("inventory_movements", "movement_type",
                    existing_type=sa.String(10), type_=sa.String(32),
                    existing_nullable=False)


def downgrade():
    op.alter_column("inventory_movements", "movement_type",
                    existing_type=sa.String(32), type_=sa.String(10),
                    existing_nullable=False)
