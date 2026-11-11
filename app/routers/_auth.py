"""Route guards. Declare one as a parameter and the route needs that kind of account:

    async def publish_event(..., _: AnyManager)
"""
import logging
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer

from app.containers import Container
from app.enums import ManagerRole
from app.exceptions import PermissionDeniedError
from app.schemas.auth import CurrentCustomerInternal, CurrentManagerInternal
from app.services.auth import AuthService

audit_logger = logging.getLogger("app.audit")

# Customers get their token from POST /auth/google, which Swagger cannot drive: paste it in
customer_bearer_scheme = HTTPBearer(scheme_name="Customer")
manager_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/admin/auth/login", scheme_name="Manager")

READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}


def client_ip(request: Request) -> str:
    # TODO: behind a reverse proxy this is the proxy; read X-Forwarded-For from a trusted hop
    return request.client.host if request.client else "unknown"


@inject
async def get_current_customer(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(customer_bearer_scheme)],
    auth_service: AuthService = Depends(Provide[Container.auth_service]),
) -> CurrentCustomerInternal:
    return auth_service.authenticate_customer(credentials.credentials)


@inject
async def get_current_manager(
    request: Request,
    token: Annotated[str, Depends(manager_oauth2_scheme)],
    auth_service: AuthService = Depends(Provide[Container.auth_service]),
) -> CurrentManagerInternal:
    manager = await auth_service.authenticate_manager(token)
    if request.method not in READ_ONLY_METHODS:
        audit_logger.info(
            "manager=%s role=%s %s %s", manager.id, manager.role, request.method, request.url.path
        )
    return manager


async def require_admin(
    manager: Annotated[CurrentManagerInternal, Depends(get_current_manager)],
) -> CurrentManagerInternal:
    if manager.role != ManagerRole.ADMIN:
        raise PermissionDeniedError("Only an admin may do this")
    return manager


Customer = Annotated[CurrentCustomerInternal, Depends(get_current_customer)]
AnyManager = Annotated[CurrentManagerInternal, Depends(get_current_manager)]
Admin = Annotated[CurrentManagerInternal, Depends(require_admin)]
