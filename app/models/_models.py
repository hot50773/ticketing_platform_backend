from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, text
from sqlmodel import Field, Relationship

from app.enums import SeatStatus, OrderStatus, PaymentStatus
from ._shared import TableBase, SoftDeletable


class User(TableBase, table=True):
    __tablename__ = "user"
    name: str
    email: str = Field(unique=True)
    hashed_password: str

# Soft-deletable tables use partial unique indexes (WHERE deleted_at IS NULL)
# rather than UNIQUE constraints: a plain constraint would let a soft-deleted
# row keep occupying its name / serial number forever, and would block the
# EventSeat renumbering that Redis offsets depend on.
SOFT_DELETE_ACTIVE = text("deleted_at IS NULL")

# Relationships are declared lazy="raise" on purpose. The default ("select")
# emits a query on first attribute access, and under asyncio that access
# happens outside the greenlet that owns the connection - the failure is a
# MissingGreenlet raised somewhere far from the code that caused it, often
# only under load. Raising instead turns "you forgot to eager-load" into an
# immediate, local error, and forces every traversal to say so at the query:
#
#     select(Order).options(selectinload(Order.tickets))
#
# See app/repositories/order.py for the loader chain the Order detail needs.
LAZY_RAISE = {"lazy": "raise"}


class Venue(SoftDeletable, TableBase, table=True):
    __tablename__ = "venue"
    name: str
    description: str
    address: str

    __table_args__ = (
        Index("uq_venue_name", "name", unique=True, postgresql_where=SOFT_DELETE_ACTIVE),
        {"comment": "場地"},
    )

class Zone(SoftDeletable, TableBase, table=True):
    __tablename__ = "zone"
    name: str
    venue_id: int = Field(foreign_key="venue.id")

    __table_args__ = (
        Index("uq_zone_venue", "venue_id", "name", unique=True, postgresql_where=SOFT_DELETE_ACTIVE),
        {"comment": "區域"},
    )

class Seat(SoftDeletable, TableBase, table=True):
    __tablename__ = "seat"
    # 0-based: this IS the Redis bitmap bit position, no arithmetic in between.
    serial_number: int
    row: int
    col: int
    zone_id: int = Field(foreign_key="zone.id")

    # (zone_id, serial_number) rather than the reverse: zone_id is the useful prefix
    __table_args__ = (
        Index("uq_seat_zone", "zone_id", "serial_number", unique=True, postgresql_where=SOFT_DELETE_ACTIVE),
        CheckConstraint("serial_number >= 0", name="ck_seat_serial_number_non_negative"),
        {"comment": "座位"},
    )

class Event(SoftDeletable, TableBase, table=True):
    __tablename__ = "event"
    name: str
    description: str
    start_at: datetime = Field(
        sa_type=DateTime(timezone=True), # type: ignore
        sa_column_kwargs={"comment": "活動開始時間"},
    )
    end_at: datetime = Field(
        sa_type=DateTime(timezone=True), # type: ignore
        sa_column_kwargs={"comment": "活動結束時間"},
    )
    is_active: bool
    venue_id: int = Field(foreign_key="venue.id")
    event_zones: list["EventZone"] = Relationship(
        back_populates="event", sa_relationship_kwargs=LAZY_RAISE
    )
    orders: list["Order"] = Relationship(
        back_populates="event", sa_relationship_kwargs=LAZY_RAISE
    )

    __table_args__ = ({"comment": "活動，例如：演唱會、研討會"},)


# -----
# ＶTODO
#  - 需修改 EventZone 和 EventSeat 的 Model，所有資訊都要複製，不要直接參考 Zone 和 Seat
#   - V Event 新增欄位：is_active
#   - V EventZone 新增欄位：ZoneName original_zone_id；移除欄位：zone_id
#   - V EventSeat 新增欄位：row col SerialNumber original_seat_id；移除欄位：seat_id
# -----
class EventZone(SoftDeletable, TableBase, table=True):
    __tablename__ = "event_zone"
    event_id: int = Field(foreign_key="event.id")
    name: str
    price: int | None = Field(
        default=None,
        sa_column_kwargs={"comment": "該分區的票價；NULL 表示尚未設定，活動不可上架"},
    )
    source_zone_id: int | None = Field(sa_column_kwargs={"comment":"後端用；記錄原始參考的 zone_id"})
    event_seats: list["EventSeat"] = Relationship(
        back_populates="event_zone", sa_relationship_kwargs=LAZY_RAISE
    )
    event: "Event" = Relationship(
        back_populates="event_zones", sa_relationship_kwargs=LAZY_RAISE
    )

    __table_args__ = (
        Index("uq_event_zone", "event_id", "name", unique=True, postgresql_where=SOFT_DELETE_ACTIVE),
        {"comment": " 活動場地的座位分區"},
    )

class EventSeat(SoftDeletable, TableBase, table=True):
    __tablename__ = "event_seat"
    event_zone_id: int = Field(foreign_key="event_zone.id")
    row: int
    col: int
    serial_number: int
    status: SeatStatus = Field(default=SeatStatus.AVAILABLE)
    source_seat_id: int | None = Field(sa_column_kwargs={"comment":"後端用；記錄原始參考的 seat_id"})

    event_zone: "EventZone" = Relationship(
        back_populates="event_seats", sa_relationship_kwargs=LAZY_RAISE
    )
    tickets: list["OrderTicket"] = Relationship(
        back_populates="event_seat", sa_relationship_kwargs=LAZY_RAISE
    )


    __table_args__ = (
        Index(
            "uq_event_seat",
            "event_zone_id",
            "serial_number",
            unique=True,
            postgresql_where=SOFT_DELETE_ACTIVE,
        ),
        CheckConstraint(
            "serial_number >= 0", name="ck_event_seat_serial_number_non_negative"
        ),
        {"comment": "活動的座位表"},
    )

class Order(SoftDeletable, TableBase, table=True):
    __tablename__ = "order"
    user_id: int = Field(foreign_key="user.id")
    event_id: int = Field(foreign_key="event.id")
    checkout_amount: int = Field(sa_column_kwargs={"comment":"結帳金額"})
    status: OrderStatus = Field(sa_column_kwargs={"server_default": OrderStatus.PENDING, "comment": "訂單完成狀態"})
    payment_status: PaymentStatus = Field(sa_column_kwargs={"server_default": PaymentStatus.PENDING, "comment": "付款狀態"})
    payment_deadline: datetime | None = Field(
        sa_type=DateTime(timezone=True), # type: ignore
        sa_column_kwargs={"comment":"付款截止期限"}
    )
    comment: str | None = Field(sa_column_kwargs={"comment":"訂單備註"})

    tickets: list["OrderTicket"] = Relationship(
        back_populates="order", sa_relationship_kwargs=LAZY_RAISE
    )
    event: "Event" = Relationship(
        back_populates="orders", sa_relationship_kwargs=LAZY_RAISE
    )


class OrderTicket(SoftDeletable, TableBase, table=True):
    __tablename__ = "order_ticket"
    order_id: int = Field(foreign_key="order.id")
    event_seat_id: int = Field(foreign_key="event_seat.id")
    amount: int = Field(sa_column_kwargs={"comment":"票價"})

    order: "Order" = Relationship(
        back_populates="tickets", sa_relationship_kwargs=LAZY_RAISE
    )
    event_seat: "EventSeat" = Relationship(
        back_populates="tickets", sa_relationship_kwargs=LAZY_RAISE
    )

    # Partial index, not a constraint: a soft-deleted ticket must stop
    # occupying its seat so the same seat can be re-added to the same order
    # after being released.
    __table_args__ = (
        Index(
            "uq_order_ticket",
            "order_id",
            "event_seat_id",
            unique=True,
            postgresql_where=SOFT_DELETE_ACTIVE,
        ),
        {"comment": "活動門票"},
    )