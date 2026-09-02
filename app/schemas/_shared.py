from datetime import datetime


class _TableMetaMixin:
    id: int
    created_at: datetime
    updated_at: datetime
