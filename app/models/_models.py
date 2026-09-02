from datetime import datetime

from sqlalchemy import UniqueConstraint, DateTime
from sqlmodel import Field

from app.enums import SeatStatus, OrderStatus, PaymentStatus
from ._shared import TableBase, SoftDeletable


class User(TableBase, table=True):
    __tablename__ = "user"
    name: str
    email: str = Field(unique=True)
    hashed_password: str

class Venue(SoftDeletable, TableBase, table=True):
    __tablename__ = "venue"
    name: str = Field(unique=True)
    description: str
    address: str

    __table_args__ = ({"comment": "場地"},)

class Zone(SoftDeletable, TableBase, table=True):
    __tablename__ = "zone"
    name: str
    venue_id: int = Field(foreign_key="venue.id")

    __table_args__ = (
        UniqueConstraint("venue_id", "name", name="uq_zone_venue"),
        {"comment": "區域"},
    )

class Seat(SoftDeletable, TableBase, table=True):
    __tablename__ = "seat"
    serial_number: int
    row: int
    col: int
    zone_id: int = Field(foreign_key="zone.id")

    __table_args__ = (
        UniqueConstraint("serial_number", "zone_id", name="uq_seat_zone"),
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
    price: int = Field(sa_column_kwargs={"comment":"該分區的票價"})
    source_zone_id: int | None = Field(sa_column_kwargs={"comment":"後端用；記錄原始參考的 zone_id"})

    __table_args__ = (
        UniqueConstraint("event_id", "name", name="uq_event_zone"),
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

    __table_args__ = (UniqueConstraint("event_zone_id", "serial_number", name="uq_event_seat"), {"comment": "活動的座位表"}, )

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


class OrderTicket(SoftDeletable, TableBase, table=True):
    __tablename__ = "order_ticket"
    order_id: int = Field(foreign_key="order.id")
    event_seat_id: int = Field(foreign_key="event_seat.id")
    amount: int = Field(sa_column_kwargs={"comment":"票價"})

    _table_args_ = (UniqueConstraint("order_id", "event_seat_id", name="uq_order_ticket"), {"comment": "活動門票"})