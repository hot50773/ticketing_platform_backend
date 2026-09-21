"""serial_number 改為 0-based

Redis bitmap 的 offset 就是 serial_number，不再需要 -1。原本的
`offset = serial_number - 1` 讓 bit 0 永遠空著，也讓每個讀取端都得記得
自己減一；把配號起點搬到 0 之後，欄位值與 bit 位置完全重合。

seat 與 event_seat 一起搬：event_seat.serial_number 是從 seat 原樣複製的，
兩張表若不同步，複製出來的活動座位表就會跟場地模板差一號。

為什麼要先拆索引
----------------
(zone_id, serial_number) 上的唯一索引是逐列檢查的，不是等整句 UPDATE 跑完
才檢查。整批平移時，某一列先搬到的位置可能正被另一列佔著 —— 例如 +1 的方向
上，0 要搬到 1，而原本的 1 還沒被搬走，就會撞 uq_seat_zone。

平移前後編號集合仍然唯一，衝突純粹來自中間狀態，所以正確的做法是把索引拆掉、
搬完再建回去。partial unique index 不能宣告成 DEFERRABLE（那是 constraint
才有的能力），沒有更省事的寫法。

Revision ID: 7c4e91ab2d38
Revises: 49be4bb8dca9
Create Date: 2026-09-05 10:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c4e91ab2d38"
down_revision: Union[str, Sequence[str], None] = "49be4bb8dca9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, unique index name, index columns) - mirrors 49be4bb8dca9
_TABLES = (
    ("seat", "uq_seat_zone", ["zone_id", "serial_number"]),
    ("event_seat", "uq_event_seat", ["event_zone_id", "serial_number"]),
)
_ACTIVE = sa.text("deleted_at IS NULL")
_CHECK_NAME = "ck_{table}_serial_number_non_negative"


def _shift(delta: str) -> None:
    """Drops the unique index, shifts every serial_number, rebuilds the index."""
    for table, index_name, columns in _TABLES:
        op.drop_index(index_name, table_name=table)
        op.execute(f"UPDATE {table} SET serial_number = serial_number {delta}")
        op.create_index(
            index_name, table, columns, unique=True, postgresql_where=_ACTIVE
        )


def upgrade() -> None:
    _shift("- 1")
    for table, _, _ in _TABLES:
        # The invariant the bitmap depends on: a negative offset is not a bit
        # position, and BITFIELD would reject it at write time - far away from
        # whatever actually produced the bad number.
        op.create_check_constraint(
            _CHECK_NAME.format(table=table), table, "serial_number >= 0"
        )


def downgrade() -> None:
    for table, _, _ in _TABLES:
        op.drop_constraint(_CHECK_NAME.format(table=table), table, type_="check")
    _shift("+ 1")
