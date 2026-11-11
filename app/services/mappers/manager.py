from app.models import Manager
from app.schemas import Page
from app.schemas.manager import ManagerResponse


class ManagerSchemaMapper:
    def to_manager(self, manager: Manager) -> ManagerResponse:
        return ManagerResponse.model_validate(manager, from_attributes=True)

    def to_manager_page(self, page: Page[Manager]) -> Page[ManagerResponse]:
        return page.map(self.to_manager)
