"""Log-in. Customers only sign in with Google; managers only with a password.
They are separate tables, so the same email can be both."""
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Form, Request, status

from app.containers import Container
from app.routers._auth import AnyManager, Customer, client_ip
from app.schemas.auth import GoogleLogin, TokenResponse
from app.schemas.customer import CustomerResponse
from app.schemas.manager import ManagerLoginForm, ManagerResponse
from app.services.auth import AuthService
from app.services.customer import CustomerService

router = APIRouter(prefix="/auth", tags=["auth"])
admin_router = APIRouter(prefix="/admin/auth", tags=["auth"])


@router.post("/google", response_model=TokenResponse, status_code=status.HTTP_200_OK)
@inject
async def login_with_google(
    payload: GoogleLogin,
    auth_service: AuthService = Depends(Provide[Container.auth_service]),
):
    """Signs a customer in with a Google ID token, registering them on first use."""
    return await auth_service.login_with_google(payload)


@router.get("/me", response_model=CustomerResponse, status_code=status.HTTP_200_OK)
@inject
async def get_me(
    customer: Customer,
    customer_service: CustomerService = Depends(Provide[Container.customer_service]),
):
    return await customer_service.get_customer(customer.id)


@admin_router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
@inject
async def login_manager(
    request: Request,
    form: Annotated[ManagerLoginForm, Form()],
    auth_service: AuthService = Depends(Provide[Container.auth_service]),
):
    """Admins and managers."""
    return await auth_service.login_manager(form.username, form.password, client_ip(request))


@admin_router.get("/me", response_model=ManagerResponse, status_code=status.HTTP_200_OK)
async def get_manager_me(manager: AnyManager):
    return manager
