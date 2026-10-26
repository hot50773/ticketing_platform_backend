"""Translation between the User aggregate's ORM rows and its API schemas."""
from app.models import User
from app.schemas import Page
from app.schemas.user import UserResponse


class UserSchemaMapper:
    def to_user(self, user: User) -> UserResponse:
        return UserResponse(
            id=user.id,
            created_at=user.created_at,
            updated_at=user.updated_at,
            name=user.name,
            email=user.email,
        )

    def to_user_page(self, page: Page[User]) -> Page[UserResponse]:
        return page.map(self.to_user)
