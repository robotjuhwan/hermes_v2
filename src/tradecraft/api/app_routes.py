from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI


@dataclass(frozen=True)
class AppRouteSpec:
    name: str
    build: Callable[[], APIRouter]


@dataclass(frozen=True)
class AppRouteRegistration:
    name: str
    paths: tuple[str, ...]


def register_app_routes(
    app: FastAPI,
    route_specs: Iterable[AppRouteSpec],
) -> list[AppRouteRegistration]:
    registrations: list[AppRouteRegistration] = []
    for route_spec in route_specs:
        router = route_spec.build()
        paths = tuple(
            str(getattr(route, "path", ""))
            for route in getattr(router, "routes", ())
            if getattr(route, "path", "")
        )
        app.include_router(router)
        registrations.append(AppRouteRegistration(name=route_spec.name, paths=paths))
    return registrations


__all__ = [
    "AppRouteRegistration",
    "AppRouteSpec",
    "register_app_routes",
]
