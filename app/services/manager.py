import asyncio

from app.enums import ManagerRole
from app.exceptions import PermissionDeniedError
from app.libs.security import PasswordHasher
from app.repositories.manager import ManagerRepository
from app.schemas import Page
from app.schemas.manager import ManagerCreate
from app.schemas.manager import ManagerCreateInternal, ManagerListParams, ManagerResponse
from app.services.mappers.manager import ManagerSchemaMapper


class ManagerService:
    def __init__(
        self,
        manager_repository: ManagerRepository,
        manager_schema_mapper: ManagerSchemaMapper,
        password_hasher: PasswordHasher,
    ):
        self._repository = manager_repository
        self._mapper = manager_schema_mapper
        self._hasher = password_hasher

    async def create_manager(self, payload: ManagerCreate, role: ManagerRole) -> ManagerResponse:
        """An admin adds managers over the API; admins only come from the CLI."""
        result = await self._repository.create_manager(
            ManagerCreateInternal(
                name=payload.name,
                email=payload.email,
                hashed_password=await asyncio.to_thread(self._hasher.hash, payload.password),
                role=role,
            )
        )
        return self._mapper.to_manager(result)

    async def get_manager(self, manager_id: int) -> ManagerResponse:
        return self._mapper.to_manager(await self._repository.get_manager(manager_id))

    async def set_manager_active(self, manager_id: int, is_active: bool) -> ManagerResponse:
        """Admins are out of reach: they only come from the CLI, and this way an
        admin cannot lock themselves or the last other admin out."""
        target = await self._repository.get_manager(manager_id)
        if target.role != ManagerRole.MANAGER:
            raise PermissionDeniedError("Admins cannot be changed over the API")
        return self._mapper.to_manager(await self._repository.set_active(manager_id, is_active))

    async def list_managers(self, params: ManagerListParams) -> Page[ManagerResponse]:
        return self._mapper.to_manager_page(await self._repository.get_manager_list(params))
