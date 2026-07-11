from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradecraft.services.jue_wiki_selection_audit import JueWikiSelectionAuditStore
from tradecraft.services.runtime_cold_archive import RuntimeColdArchiveV1


_STATUS_NAME = "status-v1.json"


def _manifest_fingerprints(root: Path) -> dict[str, dict[str, Any]]:
    paths = {
        "core": root / "manifest-v1.json",
        "jue_selection": root / "jue-selection" / "manifest-v1.json",
    }
    fingerprints: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        if not path.exists():
            fingerprints[name] = {"status": "missing"}
            continue
        data = path.read_bytes()
        fingerprints[name] = {
            "status": "present",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return fingerprints


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_runtime_cold_archive_status(
    *,
    root: Path | str,
    jue_wiki_db_path: Path | str,
) -> dict[str, Any]:
    archive_root = Path(root).expanduser().resolve()
    core = RuntimeColdArchiveV1(archive_root).status()
    jue_selection = JueWikiSelectionAuditStore(
        jue_wiki_db_path,
        archive_root,
    ).status()
    sections = {"core": core, "jue_selection": jue_selection}
    corrupt = [
        f"{section_name}:{entry_id}"
        for section_name, section in sections.items()
        for entry_id in list(section.get("corrupt_entry_ids") or [])
    ]
    return {
        "status": "warning" if corrupt else "ok",
        "root": str(archive_root),
        "entry_count": sum(
            int(section.get("entry_count") or 0) for section in sections.values()
        ),
        "archive_bytes": sum(
            int(section.get("archive_bytes") or 0) for section in sections.values()
        ),
        "corrupt_entry_ids": corrupt,
        "sections": sections,
    }


def persist_runtime_cold_archive_status(
    *,
    root: Path | str,
    jue_wiki_db_path: Path | str,
) -> dict[str, Any]:
    archive_root = Path(root).expanduser().resolve()
    payload = build_runtime_cold_archive_status(
        root=archive_root,
        jue_wiki_db_path=jue_wiki_db_path,
    )
    payload["verified_at"] = datetime.now(timezone.utc).isoformat()
    payload["manifest_fingerprints"] = _manifest_fingerprints(archive_root)
    payload["verification_snapshot"] = {"status": "current"}
    _atomic_write(archive_root / _STATUS_NAME, payload)
    return payload


def read_runtime_cold_archive_status(*, root: Path | str) -> dict[str, Any]:
    archive_root = Path(root).expanduser().resolve()
    fingerprints = _manifest_fingerprints(archive_root)
    manifests_present = any(
        row.get("status") == "present" for row in fingerprints.values()
    )
    status_path = archive_root / _STATUS_NAME
    if not status_path.exists():
        return {
            "status": "warning" if manifests_present else "ok",
            "root": str(archive_root),
            "entry_count": 0,
            "archive_bytes": 0,
            "corrupt_entry_ids": [],
            "verification_snapshot": {
                "status": "missing" if manifests_present else "not_required"
            },
        }
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {
            "status": "warning",
            "root": str(archive_root),
            "entry_count": 0,
            "archive_bytes": 0,
            "corrupt_entry_ids": [],
            "verification_snapshot": {"status": "invalid"},
        }
    stored = payload.get("manifest_fingerprints")
    if stored != fingerprints:
        payload["status"] = "warning"
        payload["verification_snapshot"] = {"status": "stale"}
        return payload
    payload["verification_snapshot"] = {"status": "current"}
    return payload
