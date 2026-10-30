import logging

from app.repositories.user import UserRepository
from app.schemas import Page, PaginationParams
from app.schemas.user import UserCreate, UserResponse
from app.services.mappers.user import UserSchemaMapper

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, user_repository: UserRepository, user_schema_mapper: UserSchemaMapper):
        self._repository = user_repository
        self._mapper = user_schema_mapper

    async def create_user(self, payload: UserCreate) -> UserResponse:
        result = await self._repository.create_user(payload)
        return self._mapper.to_user(result)

    async def list_users(self, pagination: PaginationParams) -> Page[UserResponse]:
        paged_list = await self._repository.get_users(pagination)
        return self._mapper.to_user_page(paged_list)
