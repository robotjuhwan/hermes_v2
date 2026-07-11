#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

from tradecraft.services.jue_wiki_shadow import (
    JueWikiShadowStore,
    WikiCompletionSigner,
    WikiRuntimePromptEnvelopeV1,
    WikiShadowRecordingV1,
    replay_shadow_record,
)


def _is_runtime_path(path: Path) -> bool:
    return ".runtime" in path.resolve(strict=False).parts


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short_write")
        view = view[written:]


def _pinned_output_parent(
    output_path: Path,
    *,
    allow_existing: bool = False,
) -> tuple[int, Path, str]:
    if _is_runtime_path(output_path):
        raise ValueError("live_runtime_output_forbidden")
    parent = output_path.parent.resolve(strict=True)
    if _is_runtime_path(parent):
        raise ValueError("live_runtime_output_forbidden")
    name = output_path.name
    if not name or name in {".", ".."} or "/" in name:
        raise ValueError("output_name_invalid")
    parent_fd = os.open(parent, _directory_open_flags())
    try:
        parent_stat = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise ValueError("output_parent_not_directory")
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if allow_existing:
                return parent_fd, parent, name
            raise ValueError("output_already_exists")
        return parent_fd, parent, name
    except Exception:
        os.close(parent_fd)
        raise


def _existing_database_is_safe(file_stat: os.stat_result) -> bool:
    return bool(
        stat.S_ISREG(file_stat.st_mode)
        and file_stat.st_uid == os.getuid()
        and stat.S_IMODE(file_stat.st_mode) == 0o600
        and file_stat.st_nlink == 1
    )


def _validate_existing_output(output_path: Path) -> None:
    parent_fd, _parent_path, output_name = _pinned_output_parent(
        output_path,
        allow_existing=True,
    )
    try:
        try:
            current = os.stat(
                output_name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        if not _existing_database_is_safe(current):
            raise ValueError("output_already_exists")
    finally:
        os.close(parent_fd)


def _install_comparison_database(
    output_path: Path,
    comparison: Any,
    *,
    completion_verifier: WikiCompletionSigner,
    source_recording: WikiShadowRecordingV1 | None = None,
) -> str:
    parent_fd, parent_path, output_name = _pinned_output_parent(output_path)
    stage_dir_name = f".jue-wiki-replay-{secrets.token_hex(12)}"
    stage_fd = -1
    lock_fd = -1
    installed = False
    comparison_id = comparison.comparison_id
    try:
        lock_fd = os.open(
            output_name + ".lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        if not _existing_database_is_safe(os.fstat(lock_fd)):
            raise ValueError("output_lock_unsafe")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        os.mkdir(stage_dir_name, mode=0o700, dir_fd=parent_fd)
        stage_fd = os.open(
            stage_dir_name,
            _directory_open_flags(),
            dir_fd=parent_fd,
        )
        stage_stat = os.fstat(stage_fd)
        if not stat.S_ISDIR(stage_stat.st_mode) or stage_stat.st_nlink < 2:
            raise ValueError("staging_directory_invalid")
        stage_name = "shadow.db"
        stage_file_fd = os.open(
            stage_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=stage_fd,
        )
        stage_path = parent_path / stage_dir_name / stage_name
        store = JueWikiShadowStore(
            stage_path,
            completion_verifier=completion_verifier,
            _write_uri=f"file:{quote(str(stage_path), safe='/')}?mode=rw",
        )
        try:
            store.initialize()
            if source_recording is not None:
                store.record_shadow_recording(source_recording)
            comparison_id = store.record(comparison)
            os.fsync(stage_file_fd)
        finally:
            os.close(stage_file_fd)
        file_stat = os.stat(stage_name, dir_fd=stage_fd, follow_symlinks=False)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise ValueError("staging_database_inode_invalid")
        file_fd = os.open(
            stage_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=stage_fd,
        )
        try:
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        try:
            os.link(
                stage_name,
                output_name,
                src_dir_fd=stage_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise ValueError("output_already_exists") from exc
        installed = True
        installed_stat = os.stat(
            output_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(installed_stat.st_mode)
            or installed_stat.st_ino != file_stat.st_ino
            or installed_stat.st_dev != file_stat.st_dev
            or installed_stat.st_nlink != 2
        ):
            raise ValueError("installed_database_inode_invalid")
        os.unlink(stage_name, dir_fd=stage_fd)
        final_stat = os.stat(
            output_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(final_stat.st_mode) or final_stat.st_nlink != 1:
            raise ValueError("installed_database_link_count_invalid")
        os.fsync(parent_fd)
        return comparison_id
    except Exception:
        if installed:
            try:
                os.unlink(output_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if stage_fd >= 0:
            for entry in (
                "shadow.db-wal", "shadow.db-shm", "shadow.db-journal",
                "shadow.db.lock", "shadow.db",
            ):
                try:
                    os.unlink(entry, dir_fd=stage_fd)
                except FileNotFoundError:
                    pass
            os.close(stage_fd)
        try:
            os.rmdir(stage_dir_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        if lock_fd >= 0:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        os.close(parent_fd)


def _append_comparison_database(
    output_path: Path,
    comparison: Any,
    *,
    completion_verifier: WikiCompletionSigner,
    source_recording: WikiShadowRecordingV1 | None = None,
    _before_stage_connect: Any | None = None,
) -> str:
    parent_fd, parent_path, output_name = _pinned_output_parent(
        output_path,
        allow_existing=True,
    )
    file_fd = -1
    lock_fd = -1
    stage_fd = -1
    stage_dir_name = f".jue-wiki-append-{secrets.token_hex(12)}"
    try:
        parent_before = os.fstat(parent_fd)
        lock_fd = os.open(
            output_name + ".lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        lock_stat = os.fstat(lock_fd)
        if not _existing_database_is_safe(lock_stat):
            raise ValueError("output_lock_unsafe")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        before = os.stat(output_name, dir_fd=parent_fd, follow_symlinks=False)
        if not _existing_database_is_safe(before):
            raise ValueError("output_already_exists")
        file_fd = os.open(
            output_name,
            os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        pinned = os.fstat(file_fd)
        if (pinned.st_dev, pinned.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("output_database_inode_changed")
        os.mkdir(stage_dir_name, mode=0o700, dir_fd=parent_fd)
        stage_fd = os.open(stage_dir_name, _directory_open_flags(), dir_fd=parent_fd)
        stage_name = "shadow.db"
        stage_file_fd = os.open(
            stage_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=stage_fd,
        )
        os.lseek(file_fd, 0, os.SEEK_SET)
        while chunk := os.read(file_fd, 1024 * 1024):
            _write_all(stage_file_fd, chunk)
        os.fsync(stage_file_fd)
        os.close(stage_file_fd)
        stage_path = parent_path / stage_dir_name / stage_name
        if _before_stage_connect is not None:
            _before_stage_connect()
        store = JueWikiShadowStore(
            stage_path,
            completion_verifier=completion_verifier,
            _write_uri=f"file:{quote(str(stage_path), safe='/')}?mode=rw",
        )
        store.initialize()
        if source_recording is not None:
            store.record_shadow_recording(source_recording)
        comparison_id = store.record(comparison)
        after_fd = os.fstat(file_fd)
        after_path = os.stat(
            output_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        identity = (before.st_dev, before.st_ino)
        parent_after = os.fstat(parent_fd)
        if (
            (after_fd.st_dev, after_fd.st_ino) != identity
            or (after_path.st_dev, after_path.st_ino) != identity
            or not stat.S_ISREG(after_path.st_mode)
            or after_path.st_uid != os.getuid()
            or stat.S_IMODE(after_path.st_mode) != 0o600
            or after_path.st_nlink != 1
            or (parent_after.st_dev, parent_after.st_ino)
            != (parent_before.st_dev, parent_before.st_ino)
        ):
            raise ValueError("output_database_inode_changed")
        verify_fd = os.open(stage_name, os.O_RDONLY, dir_fd=stage_fd)
        try:
            os.fsync(verify_fd)
        finally:
            os.close(verify_fd)
        os.replace(
            stage_name,
            output_name,
            src_dir_fd=stage_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
        return comparison_id
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if stage_fd >= 0:
            for entry in ("shadow.db-journal", "shadow.db.lock", "shadow.db"):
                try:
                    os.unlink(entry, dir_fd=stage_fd)
                except FileNotFoundError:
                    pass
            os.close(stage_fd)
            try:
                os.rmdir(stage_dir_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        if lock_fd >= 0:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        os.close(parent_fd)


def _read_recording(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as handle:
            payload = json.load(handle)
    finally:
        os.close(descriptor)
    if not isinstance(payload, dict):
        raise ValueError("recording_must_be_object")
    return payload


def _materialize_recording(payload: dict[str, Any]) -> WikiShadowRecordingV1:
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        payload.get("wiki_runtime_prompt_envelope")
    )
    recording = WikiShadowRecordingV1.from_run(
        venue=str(payload.get("venue") or ""),
        run_id=str(payload.get("run_id") or ""),
        manager_run_id=str(payload.get("manager_run_id") or ""),
        legacy_manager_input=dict(payload.get("manager_input") or {}),
        source_runtime_prompt=envelope.runtime_prompt(),
        final_actions=dict(payload.get("legacy_actions") or {}),
        simulate_wiki_outage=bool(payload.get("simulate_wiki_outage", True)),
        created_at=str(payload.get("recording_created_at") or ""),
    )
    if recording.recording_id != str(payload.get("recording_id") or ""):
        raise ValueError("recording_identity_mismatch")
    return recording


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay one recorded Jue Wiki manager run")
    parser.add_argument("--venue", required=True, choices=("kis", "binance"))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--recording", type=Path)
    source.add_argument("--recording-db", type=Path)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--manager-run-id", default="")
    parser.add_argument("--completion", type=Path)
    parser.add_argument(
        "--provenance-key",
        type=Path,
        help="Absolute HMAC key path used to verify persisted completions.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Do not persist the comparison (default: true)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_path = args.output
    output_parent_fd, _output_parent, _output_name = _pinned_output_parent(
        output_path,
        allow_existing=True,
    )
    os.close(output_parent_fd)
    if not args.dry_run:
        _validate_existing_output(output_path)
    completion_verifier = None
    if args.provenance_key is not None:
        completion_verifier = WikiCompletionSigner(args.provenance_key)
    elif not args.dry_run:
        from tradecraft.config import AppSettings

        completion_verifier = WikiCompletionSigner(
            Path(AppSettings().jue_wiki_provenance_key_path)
        )
    if args.recording_db is not None:
        recording_db = args.recording_db.resolve(strict=True)
        stored = JueWikiShadowStore(recording_db).recording(
            args.venue,
            run_id=args.run_id,
            manager_run_id=args.manager_run_id,
        )
        if stored is None:
            raise ValueError("stored_recording_not_found")
        recording = stored.export_payload()
        source_recording = stored
    else:
        recording = _read_recording(args.recording.resolve(strict=True))
        source_recording = _materialize_recording(recording)
    if str(recording.get("venue") or "").strip().lower() != args.venue:
        raise ValueError("recording_venue_mismatch")
    recorded_completion = recording.get("recorded_completion")
    if args.completion is not None:
        recorded_completion = _read_recording(args.completion.resolve(strict=True))
    if not isinstance(recorded_completion, dict):
        raise ValueError("recorded_completion_required")
    if (
        isinstance(recorded_completion.get("response"), dict)
        and isinstance(recorded_completion.get("provenance"), dict)
    ):
        completion_response = dict(recorded_completion["response"])
        completion_provenance = dict(recorded_completion["provenance"])
    else:
        completion_response = dict(recorded_completion)
        completion_provenance = None
    if not args.dry_run and completion_provenance is None:
        raise ValueError("verified_completion_provenance_required")
    completion_calls = 0

    def complete_json(_prompt: dict[str, Any]) -> dict[str, Any]:
        nonlocal completion_calls
        completion_calls += 1
        return json.loads(json.dumps(completion_response))

    comparison = replay_shadow_record(
        recording,
        complete_json,
        completion_provenance=completion_provenance,
        completion_verifier=completion_verifier,
    )
    if completion_calls != 1:
        raise RuntimeError("shadow_replay_completion_count_invalid")
    comparison_id = comparison.comparison_id
    if not args.dry_run:
        try:
            assert completion_verifier is not None
            comparison_id = _append_comparison_database(
                output_path,
                comparison,
                completion_verifier=completion_verifier,
                source_recording=source_recording,
            )
        except FileNotFoundError:
            comparison_id = _install_comparison_database(
                output_path,
                comparison,
                completion_verifier=completion_verifier,
                source_recording=source_recording,
            )
    print(
        json.dumps(
            {
                "status": "dry_run" if args.dry_run else "recorded",
                "comparison_id": comparison_id,
                "venue": comparison.venue,
                "snapshot_id": comparison.snapshot_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
