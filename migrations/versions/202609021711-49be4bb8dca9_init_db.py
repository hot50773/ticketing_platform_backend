"""init db

Revision ID: 49be4bb8dca9
Revises:
Create Date: 2026-09-02 17:11:59.279899

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "49be4bb8dca9"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    order_status_enum = sa.Enum("PENDING", "CANCELLED", "CONFIRMED", name="orderstatus")
    seat_status_enum = sa.Enum("AVAILABLE", "LOCKED", "RESERVED", name="seatstatus")
    payment_status_enum = sa.Enum("PENDING", "SUCCESS", "FAILED", name="paymentstatus")
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "hashed_password", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "venue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("address", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        comment="場地",
    )
    op.create_table(
        "event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "start_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="活動開始時間",
        ),
        sa.Column(
            "end_at", sa.DateTime(timezone=True), nullable=False, comment="活動結束時間"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("venue_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venue.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="活動，例如：演唱會、研討會",
    )
    op.create_table(
        "zone",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("venue_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venue.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="區域",
    )
    op.create_table(
        "event_zone",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "price",
            sa.Integer(),
            nullable=True,
            comment="該分區的票價；NULL 表示尚未設定，活動不可上架",
        ),
        sa.Column(
            "source_zone_id",
            sa.Integer(),
            nullable=True,
            comment="後端用；記錄原始參考的 zone_id",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["event.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        comment=" 活動場地的座位分區",
    )
    op.create_table(
        "order",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("checkout_amount", sa.Integer(), nullable=False, comment="結帳金額"),
        sa.Column(
            "status",
            order_status_enum,
            server_default="PENDING",
            nullable=False,
            comment="訂單完成狀態",
        ),
        sa.Column(
            "payment_status",
            payment_status_enum,
            server_default="PENDING",
            nullable=False,
            comment="付款狀態",
        ),
        sa.Column(
            "payment_deadline",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="付款截止期限",
        ),
        sa.Column(
            "comment",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
            comment="訂單備註",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["event.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "seat",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("serial_number", sa.Integer(), nullable=False),
        sa.Column("row", sa.Integer(), nullable=False),
        sa.Column("col", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zone.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="座位",
    )
    op.create_table(
        "event_seat",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_zone_id", sa.Integer(), nullable=False),
        sa.Column("row", sa.Integer(), nullable=False),
        sa.Column("col", sa.Integer(), nullable=False),
        sa.Column("serial_number", sa.Integer(), nullable=False),
        sa.Column("status", seat_status_enum, nullable=False),
        sa.Column(
            "source_seat_id",
            sa.Integer(),
            nullable=True,
            comment="後端用；記錄原始參考的 seat_id",
        ),
        sa.ForeignKeyConstraint(
            ["event_zone_id"],
            ["event_zone.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="活動的座位表",
    )
    op.create_table(
        "order_ticket",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("event_seat_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False, comment="票價"),
        sa.ForeignKeyConstraint(
            ["event_seat_id"],
            ["event_seat.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["order.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="活動門票",
    )

    # Uniqueness on soft-deletable tables is enforced by partial indexes so a
    # soft-deleted row stops occupying its name / serial number.
    active = sa.text("deleted_at IS NULL")
    op.create_index("uq_venue_name", "venue", ["name"], unique=True, postgresql_where=active)
    op.create_index("uq_zone_venue", "zone", ["venue_id", "name"], unique=True, postgresql_where=active)
    op.create_index("uq_seat_zone", "seat", ["zone_id", "serial_number"], unique=True, postgresql_where=active)
    op.create_index("uq_event_zone", "event_zone", ["event_id", "name"], unique=True, postgresql_where=active)
    op.create_index(
        "uq_event_seat", "event_seat", ["event_zone_id", "serial_number"], unique=True, postgresql_where=active
    )
    op.create_index(
        "uq_order_ticket", "order_ticket", ["order_id", "event_seat_id"], unique=True, postgresql_where=active
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("order_ticket")
    op.drop_table("event_seat")
    op.drop_table("seat")
    op.drop_table("order")
    op.drop_table("event_zone")
    op.drop_table("zone")
    op.drop_table("event")
    op.drop_table("venue")
    op.drop_table("user")
    op.execute("DROP TYPE IF EXISTS orderstatus")
    op.execute("DROP TYPE IF EXISTS seatstatus")
    op.execute("DROP TYPE IF EXISTS paymentstatus")
