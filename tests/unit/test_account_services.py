import pytest
from pydantic import ValidationError

from app.enums import ManagerRole
from app.exceptions import EntityConflictError, PermissionDeniedError
from app.libs.security import PasswordHasher
from app.schemas.auth import normalize_email
from app.schemas.manager import ManagerCreate
from app.services.mappers.manager import ManagerSchemaMapper
from app.services.manager import ManagerService
from tests.unit._fakes import FakeManagerRepository


def a_payload(email: str = "mia@example.com") -> ManagerCreate:
    return ManagerCreate(name="Mia", email=email, password="correct-horse")


def a_manager_service(repository: FakeManagerRepository) -> ManagerService:
    return ManagerService(
        manager_repository=repository,
        manager_schema_mapper=ManagerSchemaMapper(),
        password_hasher=PasswordHasher(),
    )


async def test_manager_is_stored_with_its_role_and_never_the_plain_password():
    repo = FakeManagerRepository()

    response = await a_manager_service(repo).create_manager(a_payload(), ManagerRole.MANAGER)

    stored = repo.rows[response.id]
    assert response.role == ManagerRole.MANAGER
    assert stored.hashed_password != "correct-horse"
    assert PasswordHasher().verify_and_update("correct-horse", stored.hashed_password)[0]


async def test_email_is_stored_lower_cased_so_case_cannot_open_a_second_account():
    managers = FakeManagerRepository()

    response = await a_manager_service(managers).create_manager(a_payload("Mia@Example.COM"), ManagerRole.MANAGER)

    assert response.email == "mia@example.com"
    with pytest.raises(EntityConflictError):
        await a_manager_service(managers).create_manager(a_payload("MIA@example.com"), ManagerRole.ADMIN)


async def test_admin_can_deactivate_and_reactivate_a_manager():
    managers = FakeManagerRepository()
    created = await a_manager_service(managers).create_manager(a_payload(), ManagerRole.MANAGER)

    deactivated = await a_manager_service(managers).set_manager_active(created.id, is_active=False)
    reactivated = await a_manager_service(managers).set_manager_active(created.id, is_active=True)

    assert (deactivated.is_active, reactivated.is_active) == (False, True)


async def test_admins_cannot_be_deactivated_over_the_api():
    managers = FakeManagerRepository()
    admin = await a_manager_service(managers).create_manager(a_payload(), ManagerRole.ADMIN)

    with pytest.raises(PermissionDeniedError):
        await a_manager_service(managers).set_manager_active(admin.id, is_active=False)

    assert managers.rows[admin.id].is_active


# --------------------------------------------------------------------------- #
# Email normalisation
# --------------------------------------------------------------------------- #
def test_email_ends_up_all_ascii_and_lower_case():
    """Postgres lower() disagrees with Python's on non-ASCII, so nothing non-ASCII is stored."""
    assert normalize_email("Mia@Example.COM") == "mia@example.com"
    assert normalize_email("Bob@Bücher.DE") == "bob@xn--bcher-kva.de"
    assert normalize_email("a@İSTANBUL.com").isascii()


def test_a_non_ascii_local_part_is_rejected():
    with pytest.raises(ValueError):
        normalize_email("İa@example.com")
    with pytest.raises(ValidationError):
        a_payload("josé@example.com")
