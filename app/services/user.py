"""Event lifecycle use cases: create, read, update, publish, delete.

Every use case enters the repository exactly once, through an aggregate that
owns a single transaction. Reading state and writing the decision that depends
on it therefore happen together; see app/aggregates/event.py for why the write
paths also take a row lock.

Boundaries:
  - persistence goes through the aggregate, never a session
  - domain rules go through EventRuleValidator, which stays I/O free
  - ORM -> schema translation goes through EventSchemaMapper
  - the zone / seat layout lives in EventLayoutService, not here
  - Redis is touched only where the layout freezes: publish_event warms the
    seat bitmaps, unpublish_event and delete_event drop them again
"""
import logging

from app.repositories.user import UserRepository
from app.schemas import Page, PaginationParams
from app.schemas.user import UserCreate, UserResponse
from app.services.mappers.user import UserSchemaMapper

logger = logging.getLogger(__name__)


class UserService:
    """Orchestrates the lifecycle of an event."""

    def __init__(self, user_repository: UserRepository, user_schema_mapper: UserSchemaMapper):
        self._repository = user_repository
        self._mapper = user_schema_mapper

    async def create_user(self, payload: UserCreate) -> UserResponse:
        """flow: validate holding time -> create event + clone venue layout.

        The event is always created unpublished with every zone price unset.
        """
        result = await self._repository.create_user(payload)
        return self._mapper.to_user(result)

    async def list_users(self, pagination: PaginationParams) -> Page[UserResponse]:
        """One page of non-deleted events."""
        paged_list = await self._repository.get_users(pagination)
        return self._mapper.to_user_page(paged_list)
