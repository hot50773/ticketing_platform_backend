import argparse
import asyncio
import getpass

from app.containers import Container
from app.enums import ManagerRole
from app.schemas.manager import ManagerCreate
from app.schemas.manager import ManagerResponse


async def create_admin(container: Container, payload: ManagerCreate) -> ManagerResponse:
    return await container.manager_service().create_manager(payload, ManagerRole.ADMIN)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    payload = ManagerCreate(name=args.name, email=args.email, password=getpass.getpass("Password: "))

    container = Container()
    try:
        admin = await create_admin(container, payload)
        print(f"Created admin {admin.email} (id {admin.id})")
    finally:
        await container.db().close_all_sessions()


if __name__ == "__main__":
    asyncio.run(main())
