"""Initial tables and collision protection.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(64)),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_from", sa.Numeric(12, 2), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("price_from >= 0", name="service_price_nonnegative"),
        sa.CheckConstraint("duration_minutes > 0", name="service_duration_positive"),
    )
    op.create_table(
        "business_settings",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.create_table(
        "bookings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("telegram_username", sa.String(64)),
        sa.Column("customer_name", sa.String(150), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("car", sa.String(120), nullable=False),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("booking_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("comment", sa.String(500)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "reminder_24h_sent", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "reminder_2h_sent", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("reminder_24h_claimed_at", sa.DateTime(timezone=True)),
        sa.Column("reminder_2h_claimed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("start_time < end_time", name="booking_time_order"),
        sa.CheckConstraint(
            "status IN ('pending','confirmed','cancelled','completed')", name="booking_status_valid"
        ),
    )
    op.create_index("ix_bookings_user_date", "bookings", ["telegram_user_id", "booking_date"])
    op.create_index("ix_bookings_date_status", "bookings", ["booking_date", "status"])
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("""
        ALTER TABLE bookings ADD CONSTRAINT no_overlapping_active_bookings
        EXCLUDE USING gist (
            tsrange(booking_date + start_time, booking_date + end_time, '[)') WITH &&
        ) WHERE (status IN ('pending', 'confirmed'))
    """)


def downgrade():
    op.drop_table("bookings")
    op.drop_table("business_settings")
    op.drop_table("services")
    op.drop_table("users")
