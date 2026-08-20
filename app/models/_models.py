from datetime import datetime, timezone, timedelta

from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field

from ._shared import TableBase, SoftDeletable
from app.enums import SeatStatus


class User(TableBase):
    name: str = Field(index=True, max_length=100)

class Venue(TableBase, SoftDeletable):
    name: str = Field(max_length=100)
    description: str | None = Field(max_length=1000)
    address: str = Field(max_length=100)

    __table_args__ = ({"comment": "場地"})

class Zone(TableBase, SoftDeletable):
    code: str = Field(index=True, max_length=100)
    venue_id: int = Field(foreign_key="venue.id")

    __table_args__ = (
        UniqueConstraint("code", "venue_id", name="uq_zone_venue"),
        {"comment": "區域"},
    )

class Seat(TableBase, SoftDeletable):
    serial_number: int = Field()
    row: int = Field(default=0)
    column: int = Field(default=0)
    zone_id: int = Field(foreign_key="zone.id")

    __table_args__ = (
        {"comment": "座位"},
        UniqueConstraint("serial_number", "zone_id", name="uq_seat_zone"),
    )


class Event(TableBase, SoftDeletable):
    name: str = Field(max_length=100)
    description: str = Field(default="")
    start_at: datetime = Field(sa_column_kwargs={"comment": "活動開始時間"})
    end_at: str = Field(sa_column_kwargs={"comment": "活動結束時間"})
    venue_id: int = Field(foreign_key="venue.id")

    __table_args__ = (
        {"comment": "活動，例如：演唱會、研討會"}
    )


class EventZone(TableBase, SoftDeletable):
    event_id: int = Field(foreign_key="event.id")
    zone_id: int = Field(foreign_key="zone.id")
    price: int = Field(default=0, sa_column_kwargs={"comment": "該分區的票價"})

    __table_args__ = (
        {"comment": " 活動場地的座位分區"},
        UniqueConstraint("event_id", "zone_id", name="uq_event_zone"),
    )

class EventSeat(TableBase, SoftDeletable):
    event_zone_id: int = Field(foreign_key="event_zone.id")
    seat_id: int = Field(foreign_key="seat.id")
    status: SeatStatus = Field(default=SeatStatus.RELEASED)

    __table_args__ = ({"comment": "活動的座位表（包含座位的預訂狀態）"})

# ---
# 活動A -> 折扣碼：HappyMonth -> 選兩個座位 (1-1, 1-2)
# Cart 是什麼？
## Nick:
## Cart 是指單一活動的位置, example:
## 現在有 Event A, Event B, 分別有 x, y, z 的座位
## 今天我點選 Event A, 點擊了 x, y 座位
## 又去 Event B, 點擊 z 座位
## 這時我的 cart list 會是:
### cartList:[
###   { event}
### ]
# 結帳規則：
#    V一張訂單只能結帳一個活動
# 折扣規則（可以有多種規則）：
#   套用在一張訂單？
#   套用在一個活動？ -> 活動優惠（父親節 9 折優惠，早鳥預購 9 折優惠。台新卡友優惠）
#   X 套用在特定座位？ -> 敬老、愛心票 -> 暫時不考慮


class Cart(TableBase, SoftDeletable):
    # 可以以整張訂單來做折扣
    user_id: int = Field(foreign_key="User.id")
    event_id: int =  Field(foreign_key="Event.id")
    expired_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=10), sa_column_kwargs={"comment": "購物車過期時間"}
    )

class CartTicket(TableBase):
    cart_id: int = Field(foreign_key="Cart.id")
    event_seat_id: int = Field(foreign_key="EventSeat.id")

class Order(TableBase):
    user_id: int = Field(foreign_key="user.id")
    event_id: int = Field(foreign_key="event.id")
    status: str = Field(default="PENDING", description="訂單完成狀態") # 訂單未完成、訂單已完成、訂單已取消
    payment_status: str = Field(default="PENDING", description="付款狀態") # 主流成的付款狀態：付款失敗、付款成功、待付款
    checkout_amount: int = Field(default=0, description="結帳金額", sa_column_kwargs={"comment": "結帳金額"})
    comment: str | None = Field(description="訂單備註")
    # refund_status: str | None = Field(default="") # 無退款、退款失敗、退款成功、退款中
    # last_refunded_at: datetime | None = Field(default=None)


class OrderTicket(TableBase):
    order_id: int = Field(foreign_key="Order.id")
    event_seat_id: int = Field(foreign_key="EventSeat.id")
    amount: int = Field(default=0)


# TODO: 先不管退款跟 payment（refund table, payment table）


