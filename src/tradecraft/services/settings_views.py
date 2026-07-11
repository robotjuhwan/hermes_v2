from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


_DOMAIN_PREFIXES = {
    "kis": ("kis_", "etf_", "kr_"),
    "binance": ("binance_", "upbit_", "crypto_"),
    "memory": ("investment_memory_", "live_evaluator_", "live_performance_"),
    "runtime": ("runtime_", "watchdog_"),
    "reports": ("naver_reports_", "reports_", "rag_"),
}


@dataclass(frozen=True)
class DomainSettingsView:
    domain: str
    values: Mapping[str, Any]
    aliases: Mapping[str, str]

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def as_dict(self) -> dict[str, Any]:
        return dict(self.values)


def _field_alias(field: Any) -> str:
    candidates: list[str] = []
    for source in (getattr(field, "alias", None), getattr(field, "validation_alias", None)):
        if isinstance(source, str):
            candidates.append(source)
        else:
            candidates.extend(
                str(choice)
                for choice in getattr(source, "choices", ()) or ()
                if isinstance(choice, str)
            )
    return next(
        (candidate for candidate in candidates if candidate.startswith("TRADECRAFT_")),
        candidates[0] if candidates else "",
    )


def domain_settings_view(settings: Any, domain: str) -> DomainSettingsView:
    normalized = str(domain or "").strip().lower()
    prefixes = _DOMAIN_PREFIXES.get(normalized)
    if prefixes is None:
        raise ValueError(f"unsupported settings domain: {domain}")
    model_fields = getattr(settings.__class__, "model_fields", {})
    selected = {
        name: getattr(settings, name)
        for name in model_fields
        if str(name).startswith(prefixes)
    }
    aliases = {
        name: _field_alias(model_fields[name])
        for name in selected
    }
    return DomainSettingsView(
        domain=normalized,
        values=MappingProxyType(selected),
        aliases=MappingProxyType(aliases),
    )
