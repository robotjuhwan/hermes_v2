from __future__ import annotations

from fastapi import APIRouter

from tradecraft.api.app_route_specs import RouteFactorySpec, build_route_specs


def test_build_route_specs_lazily_wraps_named_router_factories() -> None:
    calls: list[dict[str, bool]] = []
    router = APIRouter()

    @router.get("/wrapped")
    def wrapped() -> dict[str, bool]:
        return {"ok": True}

    def build_router(deps: dict[str, bool]) -> APIRouter:
        calls.append(deps)
        return router

    specs = build_route_specs(
        (
            RouteFactorySpec(
                name="wrapped",
                build_router=build_router,
                deps={"ready": True},
            ),
        )
    )

    assert calls == []
    assert [spec.name for spec in specs] == ["wrapped"]
    assert specs[0].build() is router
    assert calls == [{"ready": True}]
