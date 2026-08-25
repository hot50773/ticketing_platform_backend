from datetime import datetime, timezone, timedelta
from sqlalchemy import UniqueConstraint, DateTime
from sqlmodel import SQLModel, Field

from ._shared import TableBase, SoftDeletable
from app.enums import SeatStatus

class User(TableBase, table=True):
    __tablename__ = "user"
    name: str = Field(index=True, max_length=100)

class Venue(TableBase, SoftDeletable, table=True):
    __tablename__ = "venue"
    name: str = Field(max_length=100)
    description: str | None = Field(max_length=1000)
    address: str = Field(max_length=100)

    __table_args__ = ({"comment": "場地"},)

class Zone(TableBase, SoftDeletable, table=True):
    __tablename__ = "zone"
    code: str = Field(index=True, max_length=100)
    venue_id: int = Field(foreign_key="venue.id")

    __table_args__ = (
        UniqueConstraint("code", "venue_id", name="uq_zone_venue"),
        {"comment": "區域"},
    )

class Seat(TableBase, SoftDeletable, table=True):
    __tablename__ = "seat"
    serial_number: int = Field()
    row: int = Field(default=0)
    column: int = Field(default=0)
    zone_id: int = Field(foreign_key="zone.id")

    __table_args__ = (
        UniqueConstraint("serial_number", "zone_id", name="uq_seat_zone"),
        {"comment": "座位"},
    )

class Event(TableBase, SoftDeletable, table=True):
    __tablename__ = "event"
    name: str = Field(max_length=100)
    description: str = Field(default="")
    start_at: datetime = Field(
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"comment": "活動開始時間"}
    )
    end_at: str = Field(sa_column_kwargs={"comment": "活動結束時間"})
    venue_id: int = Field(foreign_key="venue.id")

    __table_args__ = ({"comment": "活動，例如：演唱會、研討會"},)

class EventZone(TableBase, SoftDeletable, table=True):
    __tablename__ = "event_zone"
    event_id: int = Field(foreign_key="event.id")
    zone_id: int = Field(foreign_key="zone.id")
    price: int = Field(default=0, sa_column_kwargs={"comment": "該分區的票價"})

    __table_args__ = (
        UniqueConstraint("event_id", "zone_id", name="uq_event_zone"),
        {"comment": " 活動場地的座位分區"},
    )

class EventSeat(TableBase, SoftDeletable, table=True):
    __tablename__ = "event_seat"
    event_zone_id: int = Field(foreign_key="event_zone.id")
    seat_id: int = Field(foreign_key="seat.id")
    status: SeatStatus = Field(default=SeatStatus.RELEASED)

    __table_args__ = ({"comment": "活動的座位表（包含座位的預訂狀態）"},)

class Order(TableBase, SoftDeletable, table=True):
    __tablename__ = "order"
    user_id: int = Field(foreign_key="user.id")
    event_id: int = Field(foreign_key="event.id")
    status: str = Field(default="PENDING", description="訂單完成狀態") # 訂單未完成、訂單已完成、訂單已取消
    payment_status: str = Field(default="PENDING", description="付款狀態") # 主流成的付款狀態：付款失敗、付款成功、待付款
    checkout_amount: int = Field(default=0, description="結帳金額", sa_column_kwargs={"comment": "結帳金額"})
    comment: str | None = Field(default=None, description="訂單備註")

class OrderTicket(TableBase, table=True):
    __tablename__ = "order_ticket"
    order_id: int = Field(foreign_key="order.id")
    event_seat_id: int = Field(foreign_key="event_seat.id")
    amount: int = Field(default=0)
