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

customer_router = APIRouter(prefix="/auth", tags=["auth"])
manager_router = APIRouter(prefix="/admin/auth", tags=["auth"])


@customer_router.post("/google", response_model=TokenResponse, status_code=status.HTTP_200_OK)
@inject
async def customer_google_login(
    payload: GoogleLogin,
    auth_service: AuthService = Depends(Provide[Container.auth_service]),
):
    """Signs a customer in with a Google ID token, registering them on first use."""
    return await auth_service.login_with_google(payload)


@customer_router.get("/me", response_model=CustomerResponse, status_code=status.HTTP_200_OK)
@inject
async def customer_get_me(
    customer: Customer,
    customer_service: CustomerService = Depends(Provide[Container.customer_service]),
):
    return await customer_service.get_customer(customer.id)


@manager_router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
@inject
async def manager_login(
    request: Request,
    form: Annotated[ManagerLoginForm, Form()],
    auth_service: AuthService = Depends(Provide[Container.auth_service]),
):
    """Admins and managers."""
    return await auth_service.login_manager(form.username, form.password, client_ip(request))


@manager_router.get("/me", response_model=ManagerResponse, status_code=status.HTTP_200_OK)
async def manager_get_me(manager: AnyManager):
    return manager
