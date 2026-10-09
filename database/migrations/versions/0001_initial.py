"""initial POS schema"""
from alembic import op
from app.core.db import Base
from app.models import *  # noqa: F403

revision = "0001"
down_revision = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())

