from __future__ import annotations

from fastapi import APIRouter, FastAPI

from tradecraft.api.app_routes import AppRouteSpec, register_app_routes


def test_register_app_routes_includes_named_routers_and_returns_inventory() -> None:
    first = APIRouter()
    second = APIRouter()

    @first.get("/one")
    def one() -> dict[str, bool]:
        return {"ok": True}

    @second.post("/two")
    def two() -> dict[str, bool]:
        return {"ok": True}

    app = FastAPI()

    registrations = register_app_routes(
        app,
        (
            AppRouteSpec(name="first", build=lambda: first),
            AppRouteSpec(name="second", build=lambda: second),
        ),
    )

    assert [registration.name for registration in registrations] == [
        "first",
        "second",
    ]
    assert registrations[0].paths == ("/one",)
    assert registrations[1].paths == ("/two",)
    assert {"/one", "/two"} <= {route.path for route in app.routes}
