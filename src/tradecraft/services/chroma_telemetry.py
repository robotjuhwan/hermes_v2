from __future__ import annotations

from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
from overrides import override


class NoOpProductTelemetry(ProductTelemetryClient):
    """Disable Chroma product telemetry inside the local HERMES runtime."""

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        return None
