from taskiq import TaskiqScheduler
from taskiq_redis import (
    ListRedisScheduleSource,
    RedisAsyncResultBackend,
    RedisStreamBroker,
)
from app.config import settings

# Streams rather than a plain list: a message stays pending until acked, so a
# worker killed mid-task leaves it to be redelivered instead of losing it.
broker = RedisStreamBroker(
    url=settings.redis_url,
).with_result_backend(
    RedisAsyncResultBackend(redis_url=settings.redis_url, result_ex_time=60 * 60 * 24 * 7)
)
# Holds the delayed tasks (schedule_by_time) until `taskiq scheduler` pushes
# them onto the stream. Minute resolution: a task fires up to ~1 min late.
schedule_source = ListRedisScheduleSource(url=settings.redis_url)

scheduler = TaskiqScheduler(broker=broker, sources=[schedule_source])

