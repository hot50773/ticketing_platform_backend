import pytest
from app.containers import Container

@pytest.fixture
def container():
    """Create DI Container instance."""
    return Container()

@pytest.fixture
def seat_cache(container):
    return container.event_lock_cache()

@pytest.fixture
def seat_service(container):
    return container.event_service()

# --- TDD Test Case Stub Example ---

@pytest.mark.asyncio
async def test_lock_seat_stub(seat_service):
    """Verify that calling lock_seat on the stub service raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        await seat_service.lock_seat(event_id=1, seat_id=1, user_id=101)
