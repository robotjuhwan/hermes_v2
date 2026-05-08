from __future__ import annotations

import ipaddress
from typing import Sequence

from fastapi import HTTPException, Request


_LOCALHOST_ALIASES: dict[str, str] = {
    "localhost": "127.0.0.1",
    "testclient": "127.0.0.1",
}


def _normalize_host(raw_host: str) -> str:
    host = str(raw_host or "").strip().lower()
    if not host:
        return ""
    return _LOCALHOST_ALIASES.get(host, host)


def _resolve_client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
        if forwarded:
            first = forwarded.split(",", 1)[0].strip()
            normalized = _normalize_host(first)
            if normalized:
                return normalized
    client = request.client
    host = "" if client is None else str(client.host or "").strip()
    return _normalize_host(host)


def _parse_allowed_networks(
    cidr_list: Sequence[str],
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in cidr_list:
        raw = str(item or "").strip()
        if not raw:
            continue
        try:
            out.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return out


def is_ip_allowed(ip_text: str, cidr_list: Sequence[str]) -> bool:
    target = str(ip_text or "").strip()
    if not target:
        return False
    try:
        ip_obj = ipaddress.ip_address(target)
    except ValueError:
        return False
    for network in _parse_allowed_networks(cidr_list):
        if ip_obj.version != network.version:
            continue
        if ip_obj in network:
            return True
    return False


def enforce_ui_access(
    request: Request,
    *,
    allowed_cidrs: Sequence[str],
    trust_proxy: bool,
) -> str:
    client_ip = _resolve_client_ip(request, trust_proxy=trust_proxy)
    if is_ip_allowed(client_ip, allowed_cidrs):
        return client_ip
    raise HTTPException(status_code=403, detail="ui access denied")
