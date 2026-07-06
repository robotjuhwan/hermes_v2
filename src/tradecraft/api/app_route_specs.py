from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter

from tradecraft.api.app_routes import AppRouteSpec


@dataclass(frozen=True)
class RouteFactorySpec:
    name: str
    build_router: Callable[[Any], APIRouter]
    deps: Any


def build_route_specs(
    route_factories: Iterable[RouteFactorySpec],
) -> tuple[AppRouteSpec, ...]:
    return tuple(
        AppRouteSpec(
            name=route_factory.name,
            build=_lazy_route_builder(
                route_factory.build_router,
                route_factory.deps,
            ),
        )
        for route_factory in route_factories
    )


def _lazy_route_builder(
    build_router: Callable[[Any], APIRouter],
    deps: Any,
) -> Callable[[], APIRouter]:
    return lambda: build_router(deps)


__all__ = [
    "RouteFactorySpec",
    "build_route_specs",
]
