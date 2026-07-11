from __future__ import annotations

import json
import importlib.util
import ast
from hashlib import sha256
import multiprocessing
import os
import sqlite3
import tempfile
from uuid import uuid4
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tradecraft.services.jue_wiki_shadow as shadow_module
from tradecraft.services.jue_wiki_shadow import (
    JueWikiShadowStore,
    ManagerSafetySummaryV1,
    WikiCompletionProvenanceV1,
    WikiCompletionSigner,
    WikiRuntimePromptEnvelopeV1,
    WikiShadowRecordingV1,
    WikiShadowComparisonV1,
    build_runtime_envelope_recorder,
    build_runtime_recording_recorder,
    canonical_payload_hash,
    replay_shadow_record as _production_replay_shadow_record,
)
from tradecraft.services.jue_wiki_prompt_policy import apply_jue_wiki_prompt_policy


_REPLAY_SCRIPT = Path(__file__).parents[1] / "scripts" / "replay_jue_wiki.py"
_REPLAY_SPEC = importlib.util.spec_from_file_location("replay_jue_wiki", _REPLAY_SCRIPT)
assert _REPLAY_SPEC is not None and _REPLAY_SPEC.loader is not None
_REPLAY_MODULE = importlib.util.module_from_spec(_REPLAY_SPEC)
_REPLAY_SPEC.loader.exec_module(_REPLAY_MODULE)
replay_cli_main = _REPLAY_MODULE.main
_TEST_SIGNER = WikiCompletionSigner(
    Path(tempfile.mkdtemp(prefix="tradecraft-wiki-shadow-test-")) / "provenance.key"
)


def replay_shadow_record(
    *args: object,
    completion_verifier: WikiCompletionSigner | None = _TEST_SIGNER,
    **kwargs: object,
) -> WikiShadowComparisonV1:
    return _production_replay_shadow_record(
        *args,
        completion_verifier=completion_verifier,
        **kwargs,
    )


def _force_exit_after_sqlite_commit(
    output_path: str,
    key_path: str,
    comparison: WikiShadowComparisonV1,
) -> None:
    signer = WikiCompletionSigner(Path(key_path))
    store = JueWikiShadowStore(
        Path(output_path),
        completion_verifier=signer,
        _after_comparison_insert=lambda: os._exit(91),
    )
    store.initialize()
    store.record(comparison)


def _complete_comparison(
    *,
    venue: str,
    run_id: str,
    safety_gate_loss: tuple[str, ...] = (),
    direct_raw_rag_paths: tuple[str, ...] = (),
    snapshot_id: str | None = None,
    snapshot_trace_complete: bool = True,
    wiki_induced_new_risk_expansion: bool = False,
) -> WikiShadowComparisonV1:
    return WikiShadowComparisonV1(
        run_id=run_id,
        venue=venue,  # type: ignore[arg-type]
        legacy_prompt_hash="a" * 64,
        wiki_prompt_hash="b" * 64,
        snapshot_id=snapshot_id if snapshot_id is not None else f"snapshot:{venue}:1",
        legacy_action_hash="c" * 64,
        wiki_action_hash="d" * 64,
        safety_gate_loss=safety_gate_loss,
        direct_raw_rag_paths=direct_raw_rag_paths,
        comparison_status="complete",
        created_at="2026-07-11T00:00:00+00:00",
        snapshot_trace_complete=snapshot_trace_complete,
        wiki_induced_new_risk_expansion=wiki_induced_new_risk_expansion,
        legacy_safety_summary_hash="e" * 64,
        wiki_safety_summary_hash="f" * 64,
        completion_provenance_hash="9" * 64,
        completion_provenance_verified=True,
    )


def _stored_complete_comparison(
    store: JueWikiShadowStore,
    *,
    venue: str,
    run_id: str,
    safety_gate_loss: tuple[str, ...] = (),
    direct_raw_rag_paths: tuple[str, ...] = (),
    snapshot_id: str | None = None,
    snapshot_trace_complete: bool = True,
    wiki_induced_new_risk_expansion: bool = False,
) -> WikiShadowComparisonV1:
    clean_run_id = run_id.strip()
    payload = _valid_replay_recording(venue=venue)
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        payload["wiki_runtime_prompt_envelope"]
    )
    recording = WikiShadowRecordingV1.from_run(
        venue=venue,
        run_id=clean_run_id,
        manager_run_id=clean_run_id,
        legacy_manager_input=payload["manager_input"],
        source_runtime_prompt=envelope.runtime_prompt(),
        final_actions={key: [] for key in _ACTION_KEYS_FOR_TEST},
        created_at="2026-07-11T00:00:00+00:00",
    )
    if store.completion_verifier is None:
        store.completion_verifier = _TEST_SIGNER
        store.initialize()
    store.record_shadow_recording(recording)
    exported = recording.export_payload()
    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    comparison = replay_shadow_record(
        exported,
        lambda _prompt: response,
        completion_provenance=_verified_completion_provenance(exported, response),
    )
    return replace(
        comparison,
        snapshot_id=(
            comparison.snapshot_id if snapshot_id is None else snapshot_id
        ),
        safety_gate_loss=safety_gate_loss,
        direct_raw_rag_paths=direct_raw_rag_paths,
        snapshot_trace_complete=snapshot_trace_complete,
        wiki_induced_new_risk_expansion=wiki_induced_new_risk_expansion,
    )


def _valid_replay_recording(*, venue: str = "kis") -> dict[str, object]:
    manager_input = {
        "decision_inputs": ["account", "execution_gate", "candidates", "blocks"],
        "account": {},
        "quotes": [] if venue == "kis" else {},
        "candidates": [],
        "blocks": [],
        "execution_gate": {
            "version": f"{venue}_execution_gate_v1",
            "status": "ok",
            "execute_orders": True,
            "execution_mode": "live",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
                "upbit_orders_enabled": True,
            },
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    snapshot_id = f"snapshot:{venue}:1"
    wiki_packet = {
        "version": "wiki_context_packet_v1",
        "status": "ok",
        "read_mode": "shadow",
        "snapshot_id": snapshot_id,
        "selected_pages": [],
        "rejected_page_ids": [],
        "coverage_status": "sufficient",
        "quality_warnings": [],
        "repair_required": False,
        "char_count": 2,
        "required_eligible": False,
    }
    summary = ManagerSafetySummaryV1.from_manager_input(
        venue=venue,
        manager_input=manager_input,
    )
    runtime_prompt = json.loads(json.dumps(manager_input))
    runtime_prompt["decision_inputs"] = [
        *runtime_prompt["decision_inputs"],
        "jue_wiki",
        "jue_wiki_application_coverage",
        "jue_wiki_decision_gate",
    ]
    runtime_prompt["jue_wiki_application"] = {"coverage_status": "sufficient"}
    gate = {
        "allow_new_risk": True,
        "allow_exit_actions": True,
        "reason": "wiki_context_advisory",
        "read_mode": "shadow",
        "snapshot_id": snapshot_id,
        "version": "wiki_decision_gate_v1",
    }
    runtime_prompt["jue_wiki"] = {
        "status": "ok",
        "read_mode": "shadow",
        "prompt_mode": "assist",
        "jue_wiki_context_packet": wiki_packet,
        "jue_wiki_decision_gate": gate,
        "pages": [],
    }
    runtime_prompt["jue_wiki_decision_gate"] = gate
    envelope = WikiRuntimePromptEnvelopeV1.from_runtime_prompt(
        venue=venue,
        legacy_manager_input=manager_input,
        runtime_prompt=runtime_prompt,
    )
    return {
        "run_id": f"{venue}:recorded:valid",
        "venue": venue,
        "manager_input": manager_input,
        "legacy_actions": {},
        "wiki_snapshot_id": snapshot_id,
        "wiki_runtime_prompt_envelope": envelope.to_dict(),
        "legacy_safety_summary": summary.to_dict(),
        "simulate_wiki_outage": True,
    }


def _rebind_runtime_envelope(recording: dict[str, object]) -> None:
    current = WikiRuntimePromptEnvelopeV1.from_dict(
        recording["wiki_runtime_prompt_envelope"]
    )
    runtime_prompt = current.runtime_prompt()
    manager_input = recording["manager_input"]
    for key in (
        "decision_inputs", "account", "quotes", "candidates", "blocks",
        "execution_gate", "live_authority", "entry_gate_policy", "risk_guard",
        "risk_limits", "strategy", "aggressive_opportunities",
        "direct_daily_discovery", "daily_discovery",
        "pre_adoption_symbol_analysis",
    ):
        if key in manager_input:  # type: ignore[operator]
            runtime_prompt[key] = json.loads(json.dumps(manager_input[key]))  # type: ignore[index]
    runtime_prompt["decision_inputs"] = list(
        dict.fromkeys(
            [
                *runtime_prompt.get("decision_inputs", []),
                "jue_wiki",
                "jue_wiki_application_coverage",
                "jue_wiki_decision_gate",
            ]
        )
    )
    recording["wiki_runtime_prompt_envelope"] = (
        WikiRuntimePromptEnvelopeV1.from_runtime_prompt(
            venue=str(recording["venue"]),
            legacy_manager_input=recording["manager_input"],  # type: ignore[arg-type]
            runtime_prompt=runtime_prompt,
        ).to_dict()
    )


def _valid_selected_page(*, hash_origin: str = "source") -> dict[str, object]:
    return {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "scope": "kis",
        "title": "Samsung",
        "summary": "verified summary",
        "claims": [
            {
                "claim_id": "claim:1",
                "claim_type": "fact",
                "text": "verified fact",
                "status": "verified",
                "scope": "kis",
                "evidence": [
                    {
                        "evidence_id": "evidence:1",
                        "source_type": "report",
                        "source_id": "report:1",
                        "content_hash": "a" * 64,
                        "observed_at": "2026-07-10T00:00:00+00:00",
                        "hash_origin": hash_origin,
                    }
                ],
            }
        ],
        "relationships": [
            {
                "source_claim_id": "claim:1",
                "relationship_type": "supports",
                "target_id": "kis.symbol.005930",
            }
        ],
        "status": "verified",
        "schema_version": "jue_wiki_page_v3",
        "compiler_version": "wiki_compiler_v1",
    }


def _shadow_recording(*, venue: str = "kis", index: int = 1) -> WikiShadowRecordingV1:
    payload = _valid_replay_recording(venue=venue)
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        payload["wiki_runtime_prompt_envelope"]
    )
    return WikiShadowRecordingV1.from_run(
        venue=venue,
        run_id=f"{venue}:run:{index}",
        manager_run_id=index,
        legacy_manager_input=payload["manager_input"],
        source_runtime_prompt=envelope.runtime_prompt(),
        final_actions={key: [] for key in _ACTION_KEYS_FOR_TEST},
        created_at=(
            datetime(2026, 7, 11, tzinfo=timezone.utc)
            + timedelta(microseconds=index)
        ).isoformat(),
    )


def _verified_completion_provenance(
    recording: dict[str, object],
    response: dict[str, object],
) -> WikiCompletionProvenanceV1:
    recording.setdefault(
        "recording_id",
        "wiki-recording:"
        + canonical_payload_hash(
            {
                "venue": recording.get("venue"),
                "run_id": recording.get("run_id"),
            }
        ),
    )
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        recording["wiki_runtime_prompt_envelope"]
    )
    source_prompt = envelope.runtime_prompt()
    target_prompt = apply_jue_wiki_prompt_policy(
        source_prompt,
        target_read_mode="required",
        source_to_required=True,
    )
    recording.setdefault("recording_created_at", "2026-07-11T00:00:00+00:00")
    return _TEST_SIGNER.sign(
        recording=recording,
        target_prompt=target_prompt,
        response=response,
        provider="openai",
        model="gpt-5.5",
        request_id=f"request:test:{uuid4().hex}",
    )


def _provider_completed_provenance(
    recording: dict[str, object],
    response: dict[str, object],
    *,
    request_id: str,
) -> WikiCompletionProvenanceV1:
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        recording["wiki_runtime_prompt_envelope"]
    )
    target_prompt = apply_jue_wiki_prompt_policy(
        envelope.runtime_prompt(),
        target_read_mode="required",
        source_to_required=True,
    )
    completed_response, provenance = _TEST_SIGNER.complete(
        recording=recording,
        target_prompt=target_prompt,
        complete_json=lambda _prompt: response,
        provider="openai",
        model="gpt-5.5",
        request_id=request_id,
    )
    assert completed_response == response
    return provenance
def test_shadow_recording_recorder_self_initializes_and_is_replay_exportable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    recorder = build_runtime_recording_recorder(
        db_path, completion_verifier=_TEST_SIGNER
    )
    recording = _shadow_recording()

    recording_id = recorder(recording)
    stored = recorder.recording("kis", manager_run_id="1")

    assert recording_id == recording.recording_id
    assert stored == recording
    assert stored.export_payload()["legacy_actions"] == {
        key: [] for key in _ACTION_KEYS_FOR_TEST
    }
    assert db_path.stat().st_mode & 0o777 == 0o600


def test_runtime_recording_recorder_requires_signer_and_preserves_signed_rollup(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "shadow.db"
    with pytest.raises(ValueError, match="signer_required"):
        build_runtime_recording_recorder(db_path)
    store = JueWikiShadowStore(db_path, completion_verifier=_TEST_SIGNER)
    store.initialize()
    comparison = _stored_complete_comparison(
        store, venue="kis", run_id="kis:recorder:signed-rollup"
    )
    store.record(comparison)
    before = store.eligibility("kis")
    recorder = build_runtime_recording_recorder(
        db_path,
        completion_verifier=_TEST_SIGNER,
    )

    recorder(_shadow_recording(index=99_991))

    after = store.eligibility("kis")
    assert "eligibility_signature_invalid" not in after["blockers"]
    assert after["complete_sample_count"] == before["complete_sample_count"] == 1


def test_actual_stored_shadow_recording_replays_end_to_end(tmp_path: Path) -> None:
    recorder = build_runtime_recording_recorder(
        tmp_path / "wiki.db", completion_verifier=_TEST_SIGNER
    )
    recorder(_shadow_recording())
    stored = recorder.recording("kis", manager_run_id="1")
    assert stored is not None
    calls: list[dict[str, object]] = []
    recording = stored.export_payload()
    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}

    comparison = replay_shadow_record(
        recording,
        lambda prompt: calls.append(prompt) or response,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    expected_prompt = apply_jue_wiki_prompt_policy(
        json.loads(stored.source_runtime_prompt_json),
        target_read_mode="required",
        source_to_required=True,
    )
    assert calls == [expected_prompt]
    assert comparison.comparison_status == "complete"
    assert comparison.wiki_prompt_hash == canonical_payload_hash(expected_prompt)
    packet = calls[0]["jue_wiki"]["jue_wiki_context_packet"]
    assert packet["read_mode"] == "required"
    assert packet["required_eligible"] is True
    assert calls[0]["jue_wiki_decision_gate"]["allow_new_risk"] is True
    assert calls[0]["jue_wiki_shadow_qualification_assumption"] == {
        "version": "wiki_shadow_qualification_assumption_v1",
        "source_read_mode": "shadow",
        "target_read_mode": "required",
        "assumed_required_eligible": True,
        "live_settings_changed": False,
    }


def test_shadow_recording_concurrent_duplicate_is_idempotent(tmp_path: Path) -> None:
    recorder = build_runtime_recording_recorder(
        tmp_path / "wiki.db", completion_verifier=_TEST_SIGNER
    )
    recording = _shadow_recording()

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: recorder(recording), range(16)))

    assert set(ids) == {recording.recording_id}
    assert recorder.status()["total_rows"] == 1


def test_shadow_recording_allows_reused_local_manager_id_and_rejects_ambiguous_lookup(
    tmp_path: Path,
) -> None:
    store = JueWikiShadowStore(tmp_path / "wiki.db")
    store.initialize()
    first = _shadow_recording(index=1)
    second_source = _shadow_recording(index=2)
    second = replace(second_source, manager_run_id=first.manager_run_id)

    store.record_shadow_recording(first)
    store.record_shadow_recording(second)

    assert store.recording("kis", run_id=first.run_id) == first
    assert store.recording("kis", run_id=second.run_id) == second
    with pytest.raises(ValueError, match="wiki_shadow_recording_lookup_ambiguous"):
        store.recording("kis", manager_run_id=first.manager_run_id)


def test_shadow_recording_initialization_migrates_legacy_manager_unique_constraint(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE wiki_shadow_recordings_v1 (
                recording_id TEXT PRIMARY KEY,
                venue TEXT NOT NULL CHECK (venue IN ('kis', 'binance')),
                run_id TEXT NOT NULL,
                manager_run_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                source_runtime_prompt_hash TEXT NOT NULL,
                payload_zlib BLOB NOT NULL,
                payload_hash TEXT NOT NULL,
                uncompressed_bytes INTEGER NOT NULL CHECK (uncompressed_bytes > 0),
                compressed_bytes INTEGER NOT NULL CHECK (compressed_bytes > 0),
                version TEXT NOT NULL CHECK (version = 'wiki_shadow_recording_v1'),
                created_at TEXT NOT NULL,
                UNIQUE (venue, run_id),
                UNIQUE (venue, manager_run_id)
            )
            """
        )
    store = JueWikiShadowStore(db_path)
    store.initialize()
    first = _shadow_recording(index=1)
    second = replace(_shadow_recording(index=2), manager_run_id=first.manager_run_id)

    store.record_shadow_recording(first)
    store.record_shadow_recording(second)

    assert store.recording_status()["total_rows"] == 2


@pytest.mark.parametrize("payload_kind", ["bomb", "trailing"])
def test_shadow_recording_reader_rejects_bomb_and_trailing_payloads(
    tmp_path: Path,
    payload_kind: str,
) -> None:
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path)
    store.initialize()
    recording = _shadow_recording()
    store.record_shadow_recording(recording)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER wiki_shadow_recordings_v1_no_update")
        if payload_kind == "bomb":
            payload = zlib.compress(
                b"x" * (shadow_module._SHADOW_RECORDING_MAX_UNCOMPRESSED_BYTES + 1)
            )
            uncompressed_bytes = 1
        else:
            row = conn.execute(
                "SELECT payload_zlib, uncompressed_bytes "
                "FROM wiki_shadow_recordings_v1 WHERE recording_id = ?",
                (recording.recording_id,),
            ).fetchone()
            payload = bytes(row[0]) + b"trailing-data"
            uncompressed_bytes = int(row[1])
        conn.execute(
            "UPDATE wiki_shadow_recordings_v1 "
            "SET payload_zlib = ?, compressed_bytes = ?, uncompressed_bytes = ? "
            "WHERE recording_id = ?",
            (payload, len(payload), uncompressed_bytes, recording.recording_id),
        )

    with pytest.raises(ValueError, match="wiki_shadow_recording_payload_corrupt"):
        store.recording("kis", run_id=recording.run_id)


def test_shadow_recording_reader_bounds_100mb_bomb_with_store_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "wiki.db"
    writer = JueWikiShadowStore(
        db_path,
        max_compressed_bytes=200_000,
        max_uncompressed_bytes=10_000,
    )
    writer.initialize()
    recording = _shadow_recording()
    writer.record_shadow_recording(recording)
    compressor = zlib.compressobj(level=9)
    bomb_parts = [compressor.compress(b"x" * 1_000_000) for _ in range(100)]
    bomb_parts.append(compressor.flush())
    bomb = b"".join(bomb_parts)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER wiki_shadow_recordings_v1_no_update")
        conn.execute(
            "UPDATE wiki_shadow_recordings_v1 "
            "SET payload_zlib = ?, compressed_bytes = ?, uncompressed_bytes = ? "
            "WHERE recording_id = ?",
            (bomb, len(bomb), 10_000, recording.recording_id),
        )

    real_decompressobj = zlib.decompressobj
    max_lengths: list[int] = []
    flush_calls: list[bool] = []

    class DecoderProbe:
        def __init__(self) -> None:
            self.decoder = real_decompressobj()

        def decompress(self, data: bytes, max_length: int) -> bytes:
            max_lengths.append(max_length)
            return self.decoder.decompress(data, max_length)

        def flush(self) -> bytes:
            flush_calls.append(True)
            raise AssertionError("unbounded flush must never be called")

        def __getattr__(self, name: str) -> object:
            return getattr(self.decoder, name)

    monkeypatch.setattr(shadow_module.zlib, "decompressobj", DecoderProbe)
    reader = JueWikiShadowStore(
        db_path,
        max_compressed_bytes=200_000,
        max_uncompressed_bytes=10_000,
    )

    with pytest.raises(ValueError, match="wiki_shadow_recording_payload_corrupt"):
        reader.recording("kis", run_id=recording.run_id)

    assert max_lengths == [10_001]
    assert flush_calls == []


def test_shadow_recording_writer_uses_store_instance_size_limits(tmp_path: Path) -> None:
    store = JueWikiShadowStore(
        tmp_path / "wiki.db",
        max_compressed_bytes=100,
        max_uncompressed_bytes=100,
    )
    store.initialize()

    with pytest.raises(
        ValueError,
        match="wiki_shadow_recording_uncompressed_payload_too_large",
    ):
        store.record_shadow_recording(_shadow_recording())


def test_fresh_reader_loads_authoritative_store_limits_for_large_recording(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "shadow.db"
    writer = JueWikiShadowStore(
        db_path,
        max_uncompressed_bytes=2_000_000,
        completion_verifier=_TEST_SIGNER,
    )
    writer.initialize()
    base = _valid_replay_recording()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        base["wiki_runtime_prompt_envelope"]
    )
    prompt = envelope.runtime_prompt()
    prompt["large_audit"] = "x" * 1_100_000
    recording = WikiShadowRecordingV1.from_run(
        venue="kis",
        run_id="kis:large-store-meta",
        manager_run_id="large-store-meta",
        legacy_manager_input=base["manager_input"],
        source_runtime_prompt=prompt,
        final_actions={key: [] for key in _ACTION_KEYS_FOR_TEST},
    )
    writer.record_shadow_recording(recording)

    reader = JueWikiShadowStore(db_path, completion_verifier=_TEST_SIGNER)
    stored = reader.recording("kis", run_id=recording.run_id)

    assert stored == recording
    assert reader.recording_status()["max_uncompressed_bytes_per_record"] == 2_000_000
    default_recorder = build_runtime_recording_recorder(
        db_path, completion_verifier=_TEST_SIGNER
    )
    assert default_recorder.recording("kis", run_id=recording.run_id) == recording
    assert default_recorder.status()["max_uncompressed_bytes_per_record"] == 2_000_000


def test_fd_backed_store_requires_delete_journal_and_full_sync(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    JueWikiShadowStore(db_path).initialize()
    descriptor = os.open(db_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        store = JueWikiShadowStore(
            db_path,
            _write_uri=f"file:/dev/fd/{descriptor}?mode=rw",
        )
        with store._connect() as conn:
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
            synchronous = int(conn.execute("PRAGMA synchronous").fetchone()[0])
    finally:
        os.close(descriptor)

    assert journal_mode.lower() == "delete"
    assert synchronous == 2


def test_shadow_recording_replace_and_uncontrolled_delete_are_rejected(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    recorder = build_runtime_recording_recorder(
        db_path, completion_verifier=_TEST_SIGNER
    )
    recorder(_shadow_recording())
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT * FROM wiki_shadow_recordings_v1").fetchone()
        placeholders = ",".join("?" for _ in row)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT OR REPLACE INTO wiki_shadow_recordings_v1 VALUES ({placeholders})",
                row,
            )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM wiki_shadow_recordings_v1")
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_recordings_v1"
        ).fetchone()[0] == 1


def test_runtime_envelope_replace_is_rejected_by_migration_trigger(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path)
    store.initialize()
    payload = _valid_replay_recording()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        payload["wiki_runtime_prompt_envelope"]
    )
    store.record_runtime_envelope(envelope)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT * FROM wiki_runtime_prompt_envelopes_v1").fetchone()
        placeholders = ",".join("?" for _ in row)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT OR REPLACE INTO wiki_runtime_prompt_envelopes_v1 VALUES ({placeholders})",
                row,
            )
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_runtime_prompt_envelopes_v1"
        ).fetchone()[0] == 1


def test_shadow_recording_retention_is_bounded_per_venue(tmp_path: Path) -> None:
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path)
    store.initialize()

    for index in range(605):
        store.record_shadow_recording(_shadow_recording(index=index + 1))

    status = store.recording_status()
    assert status["venues"]["kis"]["row_count"] == 600
    assert status["total_rows"] == 600
    assert store.recording("kis", manager_run_id="1") is None
    assert store.recording("kis", manager_run_id="605") is not None


def test_source_to_required_transform_is_exact_and_protects_markers_over_cap() -> None:
    payload = _valid_replay_recording()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        payload["wiki_runtime_prompt_envelope"]
    )
    source = envelope.runtime_prompt()
    source["decision_inputs"] = [f"noise_{index}" for index in range(1000)]
    source["raw_rag"] = {"secret": "must-not-cross-required-boundary"}
    source["arbitrary_dict"] = {
        "source_contract": "raw_rag",
        "secret": "dict-secret",
    }
    source["arbitrary_list"] = [
        {"source_contract": "raw_rag", "secret": "list-secret"},
        {"source_contract": "wiki", "value": "keep"},
    ]
    source_hash = canonical_payload_hash(source)

    transformed = apply_jue_wiki_prompt_policy(
        source,
        target_read_mode="required",
        source_to_required=True,
    )

    assert canonical_payload_hash(source) == source_hash
    assert "raw_rag" not in transformed
    assert transformed["jue_wiki_raw_rag_strip_audit"]["removed_path_count"] == 3
    serialized = json.dumps(transformed, ensure_ascii=False)
    assert "dict-secret" not in serialized
    assert "list-secret" not in serialized
    assert {
        "jue_wiki",
        "jue_wiki_decision_gate",
        "jue_wiki_raw_rag_strip_audit",
    }.issubset(set(transformed["decision_inputs"]))


def test_replay_never_calls_completion_when_final_raw_rag_scan_finds_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        shadow_module,
        "strip_direct_raw_rag_context",
        lambda prompt: (prompt, ("arbitrary.residual",)),
    )

    with pytest.raises(ValueError, match="wiki_shadow_target_prompt_raw_rag_residual"):
        replay_shadow_record(
            _valid_replay_recording(),
            lambda prompt: calls.append(prompt) or {},
        )

    assert calls == []


def test_advisory_policy_is_noop_without_wiki_packet() -> None:
    prompt = {"decision_inputs": ["account"], "account": {}}

    assert apply_jue_wiki_prompt_policy(
        prompt,
        target_read_mode="shadow",
    ) == prompt


def test_production_permissions_keep_create_and_exit_when_only_scale_is_blocked() -> None:
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [{"symbol": "BTCUSDT", "qty": 1}],
        "update_blocks": [{"block_id": "b1", "qty": 11}],
        "close_blocks": [{"block_id": "b1"}],
        "pause_blocks": [{"block_id": "b1"}],
    }
    blocks = [{"block_id": "b1", "symbol": "BTCUSDT", "qty": 10}]

    filtered, suppressed = shadow_module._apply_production_safety_permissions(
        actions,
        venue="binance",
        blocks=blocks,
        allow_create=True,
        allow_scale=False,
    )

    assert filtered["create_blocks"] == actions["create_blocks"]
    assert filtered["update_blocks"] == []
    assert filtered["close_blocks"] == actions["close_blocks"]
    assert filtered["pause_blocks"] == actions["pause_blocks"]
    assert suppressed == 1


def test_runtime_envelope_recorder_uses_bounded_configured_store(tmp_path: Path) -> None:
    recording = _valid_replay_recording()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        recording["wiki_runtime_prompt_envelope"]
    )
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path)
    store.initialize()
    recorder = build_runtime_envelope_recorder(db_path, max_chars=250_000)

    first_id = recorder(envelope)
    second_id = recorder(envelope)

    assert first_id == second_id
    assert recorder.db_path == db_path
    assert recorder.max_chars == 250_000
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_runtime_prompt_envelopes_v1"
        ).fetchone()[0] == 1


def test_runtime_envelope_recorder_enforces_prompt_bound(tmp_path: Path) -> None:
    recording = _valid_replay_recording()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        recording["wiki_runtime_prompt_envelope"]
    )
    store = JueWikiShadowStore(tmp_path / "wiki.db")
    store.initialize()
    recorder = build_runtime_envelope_recorder(tmp_path / "wiki.db", max_chars=10)

    with pytest.raises(ValueError, match="wiki_runtime_envelope_prompt_too_large"):
        recorder(envelope)


@pytest.mark.slow
def test_required_eligibility_is_calculated_per_venue(tmp_path: Path) -> None:
    store = JueWikiShadowStore(tmp_path / "wiki.db")
    store.initialize()
    for index in range(500):
        store.record(
            _stored_complete_comparison(store, venue="kis", run_id=f"kis:{index}")
        )

    assert store.eligibility("kis")["required_eligible"] is True
    assert store.eligibility("binance")["required_eligible"] is False


@pytest.mark.slow
def test_eligibility_fails_closed_for_each_acceptance_gate(tmp_path: Path) -> None:
    cases = (
        (
            {"safety_gate_loss": ("max_exposure",)},
            "safety_gate_divergence",
        ),
        (
            {"direct_raw_rag_paths": ("research.raw_rag",)},
            "required_mode_raw_rag_path_present",
        ),
        (
            {"snapshot_trace_complete": False},
            "snapshot_trace_incomplete",
        ),
        (
            {"wiki_induced_new_risk_expansion": True},
            "wiki_outage_new_risk_expansion",
        ),
    )
    for index, (changes, reason) in enumerate(cases):
        store = JueWikiShadowStore(tmp_path / f"wiki-{index}.db")
        store.initialize()
        for sample in range(500):
            store.record(
                _stored_complete_comparison(
                    store,
                    venue="binance",
                    run_id=f"binance:{sample}",
                )
            )
        store.record(
            replace(
                _stored_complete_comparison(
                    store,
                    venue="binance",
                    run_id=f"binance:blocker:{index}",
                ),
                **changes,
            )
        )

        result = store.eligibility("binance")

        assert result["required_eligible"] is False
        assert result["reason"] == reason


def test_incomplete_or_snapshotless_rows_do_not_count(tmp_path: Path) -> None:
    store = JueWikiShadowStore(tmp_path / "wiki.db")
    store.initialize()
    store.record(
        replace(
            _stored_complete_comparison(store, venue="kis", run_id="incomplete"),
            comparison_status="incomplete",
        )
    )
    with pytest.raises(ValueError, match="snapshot_id_required"):
        _complete_comparison(venue="kis", run_id="snapshotless", snapshot_id="")

    result = store.eligibility("kis")

    assert result["complete_sample_count"] == 0
    assert result["required_eligible"] is False
    assert result["reason"] == "insufficient_complete_comparisons"


def test_record_is_idempotent_but_identity_collision_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path)
    store.initialize()
    comparison = _stored_complete_comparison(store, venue="kis", run_id="kis:1")

    first = store.record(comparison)
    second = store.record(comparison)

    assert first == second
    with pytest.raises(ValueError, match="wiki_shadow_identity_collision"):
        store.record(replace(comparison, wiki_action_hash="f" * 64))
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wiki_shadow_comparisons_v1").fetchone() == (1,)


def test_comparison_normalizes_semantic_identity_and_set_fields(tmp_path: Path) -> None:
    store = JueWikiShadowStore(tmp_path / "wiki.db")
    store.initialize()
    first = replace(
        _stored_complete_comparison(store, venue="kis", run_id=" kis:1 "),
        venue=" KIS ",  # type: ignore[arg-type]
        safety_gate_loss=(" Kill_Switch ", "max_exposure"),
        direct_raw_rag_paths=("RAW_RAG", "raw_rag"),
        legacy_prompt_hash="A" * 64,
    )
    semantic_replay = replace(
        first,
        run_id="kis:1",
        venue="kis",
        safety_gate_loss=("MAX_EXPOSURE", "kill_switch"),
        direct_raw_rag_paths=("raw_rag",),
        legacy_prompt_hash="a" * 64,
    )

    assert store.record(first) == store.record(semantic_replay)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("version", "wiki_shadow_comparison_v2", "version_invalid"),
        ("snapshot_id", "   ", "snapshot_id_required"),
        ("snapshot_trace_complete", 1, "snapshot_trace_complete_must_be_bool"),
        ("simulated_wiki_outage", "true", "simulated_wiki_outage_must_be_bool"),
    ],
)
def test_comparison_rejects_malformed_contract_fields(
    field: str,
    value: object,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        replace(
            _complete_comparison(venue="kis", run_id="kis:malformed"),
            **{field: value},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("safety_gate_loss", ("kill_switch",)),
        ("direct_raw_rag_paths", ("raw_rag",)),
        ("snapshot_trace_complete", False),
        ("wiki_induced_new_risk_expansion", True),
        ("simulated_wiki_outage", False),
        ("wiki_read_mode", "prefer"),
        ("candidate_delta", ("candidate:c1",)),
        ("action_delta", ("create_blocks",)),
        ("safety_gate_delta", ("max_exposure",)),
        ("removed_raw_rag_paths", ("research.raw_rag",)),
        ("legacy_safety_summary_hash", "1" * 64),
        ("wiki_safety_summary_hash", "2" * 64),
    ],
)
def test_collision_hash_covers_every_safety_relevant_field(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    store = JueWikiShadowStore(tmp_path / f"{field}.db")
    store.initialize()
    comparison = _stored_complete_comparison(
        store,
        venue="kis",
        run_id="kis:safety",
    )
    store.record(comparison)

    with pytest.raises(ValueError, match="wiki_shadow_identity_collision"):
        store.record(replace(comparison, **{field: value}))


def test_store_initialization_is_additive_to_existing_wiki_database(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE legacy_wiki_pages (page_id TEXT PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO legacy_wiki_pages VALUES ('page:1', 'unchanged')")
        before_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'legacy_wiki_pages'"
        ).fetchone()[0]

    JueWikiShadowStore(db_path).initialize()

    with sqlite3.connect(db_path) as conn:
        after_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'legacy_wiki_pages'"
        ).fetchone()[0]
        legacy_rows = conn.execute("SELECT * FROM legacy_wiki_pages").fetchall()
    assert after_sql == before_sql
    assert legacy_rows == [("page:1", "unchanged")]


def test_comparison_rows_are_database_enforced_immutable(tmp_path: Path) -> None:
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path)
    store.initialize()
    comparison = _stored_complete_comparison(
        store,
        venue="kis",
        run_id="kis:immutable",
    )
    store.record(comparison)

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE wiki_shadow_comparisons_v1 SET snapshot_id = 'changed'"
            )
        with pytest.raises(sqlite3.Error):
            conn.execute("DELETE FROM wiki_shadow_comparisons_v1")


def test_direct_sql_replace_cannot_desynchronize_source_and_rollup(tmp_path: Path) -> None:
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path)
    store.initialize()
    comparison = _stored_complete_comparison(
        store,
        venue="kis",
        run_id="kis:replace",
    )
    store.record(comparison)
    before = store.eligibility("kis")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM wiki_shadow_comparisons_v1 WHERE comparison_id = ?",
            (comparison.comparison_id,),
        ).fetchone()
        placeholders = ",".join("?" for _ in row)
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
            conn.execute(
                f"INSERT OR REPLACE INTO wiki_shadow_comparisons_v1 VALUES ({placeholders})",
                tuple(row),
            )

    assert store.eligibility("kis")["complete_sample_count"] == before["complete_sample_count"]


def test_eligibility_status_read_does_not_modify_database(tmp_path: Path) -> None:
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path)
    store.initialize()
    store.record(
        _stored_complete_comparison(store, venue="kis", run_id="kis:read-only")
    )
    before = (sha256(db_path.read_bytes()).hexdigest(), db_path.stat().st_mtime_ns)

    result = store.eligibility("kis")

    assert result["venue"] == "kis"
    assert (sha256(db_path.read_bytes()).hexdigest(), db_path.stat().st_mtime_ns) == before


def test_replay_calls_completion_once_and_never_uses_executor() -> None:
    calls: list[dict[str, object]] = []
    recording = _valid_replay_recording()
    exact_input = json.loads(json.dumps(recording["manager_input"]))
    exact_input["decision_inputs"].append("research")
    exact_input["candidates"] = [{"candidate_id": "c1", "symbol": "005930"}]
    exact_input["research"] = {"raw_rag": [{"title": "nested legacy report"}]}
    exact_input["raw_rag"] = [{"title": "legacy report"}]
    recording["run_id"] = "kis:recorded:1"
    recording["manager_input"] = exact_input
    recording["legacy_safety_summary"] = ManagerSafetySummaryV1.from_manager_input(
        venue="kis", manager_input=exact_input
    ).to_dict()
    _rebind_runtime_envelope(recording)

    def complete_json(prompt: dict[str, object]) -> dict[str, object]:
        calls.append(prompt)
        return response

    response = {
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
            "adopt_existing_blocks": [],
            "safety_gates": ["kill_switch", "max_exposure"],
            "selected_candidate_ids": ["c1"],
    }

    result = replay_shadow_record(
        recording,
        complete_json,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    assert len(calls) == 1
    assert result.comparison_status == "complete"
    assert result.legacy_prompt_hash == canonical_payload_hash(exact_input)
    assert result.snapshot_id == "snapshot:kis:1"
    assert result.direct_raw_rag_paths == ()
    assert result.removed_raw_rag_paths == ()
    assert "raw_rag" not in calls[0]
    assert "research" not in calls[0]
    assert calls[0]["jue_wiki"]["jue_wiki_context_packet"]["snapshot_id"] == (
        "snapshot:kis:1"
    )
    assert result.safety_gate_loss == ()


def test_replay_without_completion_provenance_is_incomplete() -> None:
    calls: list[dict[str, object]] = []

    result = replay_shadow_record(
        _valid_replay_recording(),
        lambda prompt: calls.append(prompt)
        or {key: [] for key in _ACTION_KEYS_FOR_TEST},
    )

    assert len(calls) == 1
    assert result.comparison_status == "incomplete"
    assert "completion_provenance_missing" in result.safety_gate_loss


def test_replay_requires_exact_verified_completion_provenance() -> None:
    stored = _shadow_recording(index=91)
    recording = stored.export_payload()
    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    provenance = _verified_completion_provenance(recording, response)

    verified = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=provenance,
    )
    mismatched = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=replace(
            provenance,
            request_id="request:test:bad",
            response_hash="f" * 64,
        ),
    )

    assert verified.comparison_status == "complete"
    assert verified.completion_provenance_verified is True
    assert verified.completion_provenance_hash == provenance.provenance_hash
    assert mismatched.comparison_status == "incomplete"
    assert "completion_provenance_mismatch" in mismatched.safety_gate_loss


def test_completion_signer_wraps_exactly_one_completion_and_detects_tampering(
    tmp_path: Path,
) -> None:
    signer_type = getattr(shadow_module, "WikiCompletionSigner", None)
    assert signer_type is not None
    signer = signer_type(tmp_path / "keys" / "provenance.key")
    recording = _shadow_recording(index=92).export_payload()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        recording["wiki_runtime_prompt_envelope"]
    )
    target_prompt = apply_jue_wiki_prompt_policy(
        envelope.runtime_prompt(),
        target_read_mode="required",
        source_to_required=True,
    )
    calls: list[dict[str, object]] = []

    response, provenance = signer.complete(
        recording=recording,
        target_prompt=target_prompt,
        complete_json=lambda prompt: calls.append(prompt)
        or {key: [] for key in _ACTION_KEYS_FOR_TEST},
        provider="openai",
        model="gpt-5.5",
        request_id="request:signer:92",
    )

    assert calls == [target_prompt]
    assert signer.key_path.stat().st_mode & 0o777 == 0o600
    assert signer.verify(
        provenance,
        recording=recording,
        target_prompt=target_prompt,
        response=response,
    ) is True
    assert signer.verify(
        replace(provenance, response_hash="f" * 64),
        recording=recording,
        target_prompt=target_prompt,
        response=response,
    ) is False


def test_shadow_store_and_signer_reject_relative_runtime_and_inode_aliases(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="db_path_must_be_absolute"):
        JueWikiShadowStore(Path("relative.db"))
    with pytest.raises(ValueError, match="key_path_must_be_absolute"):
        WikiCompletionSigner(Path("relative.key"))
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    with pytest.raises(ValueError, match="db_path_runtime_forbidden"):
        JueWikiShadowStore(runtime / "shadow.db")
    with pytest.raises(ValueError, match="key_path_runtime_forbidden"):
        WikiCompletionSigner(runtime / "provenance.key")

    source = tmp_path / "source.db"
    source.write_bytes(b"sqlite-placeholder")
    alias = tmp_path / "alias.db"
    os.link(source, alias)
    with pytest.raises(ValueError, match="db_file_unsafe"):
        JueWikiShadowStore(alias)
    symlink = tmp_path / "symlink.db"
    symlink.symlink_to(source)
    with pytest.raises(ValueError, match="db_file_unsafe"):
        JueWikiShadowStore(symlink)


def test_signer_rejects_unsafe_parent_and_store_pins_key_id_immutably(
    tmp_path: Path,
) -> None:
    unsafe_parent = tmp_path / "unsafe"
    unsafe_parent.mkdir(mode=0o777)
    unsafe_parent.chmod(0o777)
    with pytest.raises(ValueError, match="key_parent_unsafe"):
        WikiCompletionSigner(unsafe_parent / "key")

    signer_a = WikiCompletionSigner(tmp_path / "safe-a" / "key")
    signer_b = WikiCompletionSigner(tmp_path / "safe-b" / "key")
    db_path = tmp_path / "shadow.db"
    JueWikiShadowStore(db_path, completion_verifier=signer_a).initialize()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT key_id FROM wiki_shadow_store_meta_v1"
        ).fetchone() == (signer_a.key_id,)
        with pytest.raises(sqlite3.Error):
            conn.execute(
                "UPDATE wiki_shadow_store_meta_v1 SET key_id = ?",
                (signer_b.key_id,),
            )
        with pytest.raises(sqlite3.Error):
            conn.execute("DELETE FROM wiki_shadow_store_meta_v1")
    with pytest.raises(ValueError, match="key_mismatch"):
        JueWikiShadowStore(
            db_path,
            completion_verifier=signer_b,
        ).initialize()


def test_replay_accepts_only_key_verified_completion_provenance(tmp_path: Path) -> None:
    signer = WikiCompletionSigner(tmp_path / "provenance.key")
    recording = _shadow_recording(index=93).export_payload()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        recording["wiki_runtime_prompt_envelope"]
    )
    target_prompt = apply_jue_wiki_prompt_policy(
        envelope.runtime_prompt(),
        target_read_mode="required",
        source_to_required=True,
    )
    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    provenance = signer.sign(
        recording=recording,
        target_prompt=target_prompt,
        response=response,
        provider="openai",
        model="gpt-5.5",
        request_id="request:replay:93",
    )

    verified = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=provenance,
        completion_verifier=signer,
    )
    tampered = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=replace(provenance, signature="f" * 64),
        completion_verifier=signer,
    )

    assert verified.comparison_status == "complete"
    assert tampered.comparison_status == "incomplete"


def test_bad_replay_attempt_does_not_claim_recording_before_signed_retry(
    tmp_path: Path,
) -> None:
    signer = WikiCompletionSigner(tmp_path / "provenance.key")
    store = JueWikiShadowStore(
        tmp_path / "shadow.db",
        completion_verifier=signer,
    )
    store.initialize()
    recording = _shadow_recording(index=94)
    store.record_shadow_recording(recording)
    exported = recording.export_payload()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        exported["wiki_runtime_prompt_envelope"]
    )
    target_prompt = apply_jue_wiki_prompt_policy(
        envelope.runtime_prompt(),
        target_read_mode="required",
        source_to_required=True,
    )
    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    bad_provenance = replace(
        signer.sign(
            recording=exported,
            target_prompt=target_prompt,
            response=response,
            provider="openai",
            model="gpt-5.5",
            request_id="request:attempt:94",
        ),
        signature="f" * 64,
    )
    bad = replay_shadow_record(
        exported,
        lambda _prompt: response,
        completion_provenance=bad_provenance,
        completion_verifier=signer,
    )

    attempt_id = store.record(bad)

    good_provenance = signer.sign(
        recording=exported,
        target_prompt=target_prompt,
        response=response,
        provider="openai",
        model="gpt-5.5",
        request_id="request:attempt:94",
    )
    good = replay_shadow_record(
        exported,
        lambda _prompt: response,
        completion_provenance=good_provenance,
        completion_verifier=signer,
    )
    comparison_id = store.record(good)

    assert attempt_id.startswith("wiki-shadow-attempt:")
    assert comparison_id == good.comparison_id
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_replay_attempts_v1"
        ).fetchone() == (1,)
        attempt_row = conn.execute(
            "SELECT request_id, payload_json FROM wiki_shadow_replay_attempts_v1"
        ).fetchone()
        assert attempt_row[0] == ""
        assert "request:attempt:94" in attempt_row[1]
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE wiki_shadow_replay_attempts_v1 SET request_id = 'changed'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM wiki_shadow_replay_attempts_v1")
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_comparisons_v1"
        ).fetchone() == (1,)


def test_direct_sql_complete_insert_requires_registered_key_verifier(
    tmp_path: Path,
) -> None:
    signer = WikiCompletionSigner(tmp_path / "provenance.key")
    store = JueWikiShadowStore(
        tmp_path / "shadow.db",
        completion_verifier=signer,
    )
    store.initialize()
    recording = _shadow_recording(index=95)
    store.record_shadow_recording(recording)
    exported = recording.export_payload()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        exported["wiki_runtime_prompt_envelope"]
    )
    target_prompt = apply_jue_wiki_prompt_policy(
        envelope.runtime_prompt(),
        target_read_mode="required",
        source_to_required=True,
    )
    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    provenance = signer.sign(
        recording=exported,
        target_prompt=target_prompt,
        response=response,
        provider="openai",
        model="gpt-5.5",
        request_id="request:direct-sql:95",
    )
    comparison = replay_shadow_record(
        exported,
        lambda _prompt: response,
        completion_provenance=provenance,
        completion_verifier=signer,
    )

    with sqlite3.connect(store.db_path) as conn:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(wiki_shadow_comparisons_v1)")
        }
        assert {
            "recording_id",
            "completion_provenance_json",
            "completion_signature",
            "completion_request_id",
        }.issubset(columns)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                """
                INSERT INTO wiki_shadow_comparisons_v1 (
                    comparison_id, run_id, venue, snapshot_id,
                    comparison_status, wiki_read_mode, snapshot_trace_complete,
                    wiki_induced_new_risk_expansion, simulated_wiki_outage,
                    completion_provenance_verified, recording_id,
                    completion_provenance_json, completion_signature,
                    completion_request_id, safety_gate_loss_json,
                    direct_raw_rag_paths_json, payload_json, payload_hash,
                    version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    comparison.comparison_id,
                    comparison.run_id,
                    comparison.venue,
                    comparison.snapshot_id,
                    "complete",
                    comparison.wiki_read_mode,
                    1,
                    0,
                    1,
                    1,
                    comparison.recording_id,
                    comparison.completion_provenance_json,
                    provenance.signature,
                    provenance.request_id,
                    "[]",
                    "[]",
                    json.dumps(comparison.to_dict()),
                    canonical_payload_hash(comparison.to_dict()),
                    comparison.version,
                    comparison.created_at,
                ),
            )


def test_initialize_quarantines_cryptographically_invalid_complete_row(
    tmp_path: Path,
) -> None:
    signer = _TEST_SIGNER
    store = JueWikiShadowStore(
        tmp_path / "shadow.db",
        completion_verifier=signer,
    )
    store.initialize()
    comparison = _stored_complete_comparison(
        store,
        venue="kis",
        run_id="kis:quarantine:invalid-signature",
    )
    store.record(comparison)
    provenance = json.loads(comparison.completion_provenance_json)
    provenance["signature"] = "f" * 64
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TRIGGER wiki_shadow_comparisons_v1_no_update")
        conn.execute(
            "UPDATE wiki_shadow_comparisons_v1 "
            "SET completion_provenance_json = ?, completion_signature = ?",
            (json.dumps(provenance), "f" * 64),
        )

    store.initialize()

    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_comparisons_v1"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_replay_attempts_v1"
        ).fetchone() == (1,)
    assert store.eligibility("kis")["required_eligible"] is False


def test_eligibility_rollup_rejects_direct_updates_and_bad_signature(
    tmp_path: Path,
) -> None:
    signer = WikiCompletionSigner(tmp_path / "provenance.key")
    store = JueWikiShadowStore(
        tmp_path / "shadow.db",
        completion_verifier=signer,
    )
    store.initialize()
    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(sqlite3.Error):
            conn.execute(
                "UPDATE wiki_shadow_eligibility_v1 "
                "SET complete_sample_count = 500 WHERE venue = 'kis'"
            )
        conn.execute("DROP TRIGGER wiki_shadow_eligibility_v1_no_update")
        conn.execute(
            "UPDATE wiki_shadow_eligibility_v1 "
            "SET complete_sample_count = 500, signature = 'forged' "
            "WHERE venue = 'kis'"
        )
    result = store.eligibility("kis")
    assert result["required_eligible"] is False
    assert "eligibility_signature_invalid" in result["blockers"]


def test_r7_unverified_comparison_migrates_losslessly_to_attempt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "shadow.db"
    payload = _complete_comparison(
        venue="kis",
        run_id="kis:r7-unverified",
    ).to_dict()
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE wiki_shadow_comparisons_v1 (
                comparison_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                venue TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                comparison_status TEXT NOT NULL,
                wiki_read_mode TEXT NOT NULL,
                snapshot_trace_complete INTEGER NOT NULL,
                wiki_induced_new_risk_expansion INTEGER NOT NULL,
                simulated_wiki_outage INTEGER NOT NULL,
                completion_provenance_verified INTEGER NOT NULL DEFAULT 0,
                safety_gate_loss_json TEXT NOT NULL,
                direct_raw_rag_paths_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                version TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_shadow_comparisons_v1 VALUES (
                ?, ?, ?, ?, 'complete', 'required', 1, 0, 1, 1,
                '[]', '[]', ?, ?, 'wiki_shadow_comparison_v1', ?
            )
            """,
            (
                "wiki-shadow:r7-unverified",
                "kis:r7-unverified",
                "kis",
                "snapshot:kis:r7",
                payload_json,
                canonical_payload_hash(payload),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    signer = WikiCompletionSigner(tmp_path / "provenance.key")
    store = JueWikiShadowStore(db_path, completion_verifier=signer)

    store.initialize()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_comparisons_v1"
        ).fetchone() == (0,)
        attempt = conn.execute(
            "SELECT run_id, payload_json FROM wiki_shadow_replay_attempts_v1"
        ).fetchone()
    assert attempt[0] == "kis:r7-unverified"
    assert json.loads(attempt[1])["run_id"] == "kis:r7-unverified"


def test_replay_ignores_model_self_reported_gates_and_candidates() -> None:
    recording = _valid_replay_recording(venue="binance")
    recording["manager_input"] = {
        **recording["manager_input"],  # type: ignore[dict-item]
        "candidates": [
            {"candidate_id": "real", "symbol": "BTCUSDT"},
            {"candidate_id": "local", "symbol": "ETHUSDT"},
        ],
    }
    recording["legacy_safety_summary"] = ManagerSafetySummaryV1.from_manager_input(
        venue="binance",
        manager_input=recording["manager_input"],  # type: ignore[arg-type]
    ).to_dict()
    _rebind_runtime_envelope(recording)
    recording["production_gates"] = {
        "execution_gate": {
            "status": "blocked",
            "execute_orders": False,
            "kill_switch": {"enabled": True},
        }
    }

    response = {
            "create_blocks": [{"symbol": "ETHUSDT", "qty": 1}],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
            "adopt_existing_blocks": [],
            "safety_gates": ["everything_passed"],
            "selected_candidate_ids": ["real"],
    }
    result = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    assert result.safety_gate_loss == ()
    assert result.candidate_delta == ("candidate_ref_changed:local",)
    assert result.wiki_induced_new_risk_expansion is False


def test_replay_server_blocks_kill_switch_new_risk_and_marks_sample() -> None:
    recording = _valid_replay_recording(venue="binance")
    manager = recording["manager_input"]
    manager["candidates"] = [
        {
            "candidate_id": "c1",
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
        }
    ]
    manager["execution_gate"]["status"] = "blocked"
    manager["execution_gate"]["kill_switch"]["enabled"] = True
    recording["legacy_safety_summary"] = ManagerSafetySummaryV1.from_manager_input(
        venue="binance", manager_input=manager
    ).to_dict()
    _rebind_runtime_envelope(recording)

    response = {
            "create_blocks": [
                {
                    "candidate_id": "c1",
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "qty": 1,
                }
            ],
            "update_blocks": [], "close_blocks": [], "pause_blocks": [],
            "adopt_existing_blocks": [],
    }
    result = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    assert result.comparison_status == "complete"
    assert "server_blocked_new_risk_proposal" in result.safety_gate_loss
    assert "server_blocked_new_risk_proposal" in result.safety_gate_delta
    assert result.wiki_induced_new_risk_expansion is False


@pytest.mark.parametrize(
    ("venue", "safety_section"),
    [
        ("kis", {"live_authority": {"status": "error", "error_message": "db down"}}),
        (
            "binance",
            {
                "live_authority": {
                    "status": "ok",
                    "validation_gate": {
                        "status": "blocked_by_validation",
                        "readiness": "blocked_by_validation",
                        "risk_governor_action": "halt_new_risk",
                    },
                }
            },
        ),
    ],
)
def test_replay_production_safety_blocks_reviewer_venue_cases(
    venue: str,
    safety_section: dict[str, object],
) -> None:
    recording = _valid_replay_recording(venue=venue)
    manager = recording["manager_input"]
    manager.update(safety_section)
    if venue == "binance":
        manager["candidates"] = [
            {"candidate_id": "c1", "symbol": "BTCUSDT", "market": "spot", "side": "long"}
        ]
        create = {
            "candidate_id": "c1", "symbol": "BTCUSDT", "market": "spot",
            "side": "long", "qty": 1,
        }
    else:
        manager["strategy"] = {
            "aggressive_opportunities": [
                {"symbol": "005930", "market": "krx", "side": "long"}
            ]
        }
        create = {
            "symbol": "005930", "qty": 1, "target_price": 110, "stop_price": 90,
        }
    recording["legacy_safety_summary"] = ManagerSafetySummaryV1.from_manager_input(
        venue=venue, manager_input=manager
    ).to_dict()
    _rebind_runtime_envelope(recording)

    result = replay_shadow_record(
        recording,
        lambda _prompt: {
            "create_blocks": [create],
            "update_blocks": [], "close_blocks": [], "pause_blocks": [],
            "adopt_existing_blocks": [],
        },
    )

    assert "server_blocked_new_risk_proposal" in result.safety_gate_loss
    assert result.wiki_induced_new_risk_expansion is False


def test_duplicate_candidate_id_is_invalid_even_when_rows_are_identical() -> None:
    recording = _valid_replay_recording(venue="binance")
    candidate = {
        "candidate_id": "duplicate", "symbol": "BTCUSDT", "market": "spot", "side": "long"
    }
    recording["manager_input"]["candidates"] = [candidate, dict(candidate)]
    recording["legacy_safety_summary"] = ManagerSafetySummaryV1.from_manager_input(
        venue="binance", manager_input=recording["manager_input"]
    ).to_dict()
    _rebind_runtime_envelope(recording)

    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    result = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    assert result.comparison_status == "incomplete"
    assert "candidate_id_duplicate:duplicate" in result.candidate_delta


def test_kis_identical_derived_candidates_from_overlapping_sources_are_deduplicated(
) -> None:
    recording = _valid_replay_recording(venue="kis")
    candidate = {"symbol": "005930", "market": "krx", "side": "long"}
    manager = recording["manager_input"]
    manager["strategy"] = {"aggressive_opportunities": [candidate]}
    manager["direct_daily_discovery"] = {"candidates": [dict(candidate)]}
    manager["decision_inputs"].extend(["strategy", "direct_daily_discovery"])
    recording["legacy_safety_summary"] = ManagerSafetySummaryV1.from_manager_input(
        venue="kis",
        manager_input=manager,
    ).to_dict()
    _rebind_runtime_envelope(recording)

    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    result = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    assert result.comparison_status == "complete"
    assert result.candidate_delta == ()


def test_replay_envelope_hash_binding_mismatch_is_incomplete() -> None:
    recording = _valid_replay_recording(venue="kis")
    envelope = dict(recording["wiki_runtime_prompt_envelope"])
    envelope["legacy_manager_input_hash"] = "f" * 64
    recording["wiki_runtime_prompt_envelope"] = envelope

    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    result = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    assert result.comparison_status == "incomplete"
    assert result.snapshot_trace_complete is False


def test_replay_missing_local_enforcement_inputs_is_incomplete() -> None:
    recording = _valid_replay_recording()
    recording.pop("legacy_safety_summary")

    result = replay_shadow_record(
        recording,
        lambda _prompt: {key: [] for key in _ACTION_KEYS_FOR_TEST},
    )

    assert result.comparison_status == "incomplete"
    assert "enforcement_inputs_missing" in result.safety_gate_loss


@pytest.mark.parametrize(
    "update",
    [
        {"block_id": "b1", "qty": 11},
        {"block_id": "b1", "target_notional_usdt": 1100},
        {"block_id": "b1", "leverage": 4},
    ],
)
def test_binance_outage_suppresses_objective_risk_increase_with_shared_gate(
    update: dict[str, object],
) -> None:
    recording = _valid_replay_recording(venue="binance")
    recording["manager_input"] = {
        **recording["manager_input"],  # type: ignore[dict-item]
        "blocks": [
            {
                "block_id": "b1",
                "symbol": "BTCUSDT",
                "market": "futures",
                "qty": 10,
                "notional_usdt": 1000,
                "leverage": 3,
            }
        ],
    }
    recording["legacy_safety_summary"] = ManagerSafetySummaryV1.from_manager_input(
        venue="binance",
        manager_input=recording["manager_input"],  # type: ignore[arg-type]
    ).to_dict()
    _rebind_runtime_envelope(recording)

    result = replay_shadow_record(
        recording,
        lambda _prompt: {
            "create_blocks": [],
            "update_blocks": [update],
            "close_blocks": [],
            "pause_blocks": [],
            "adopt_existing_blocks": [],
        },
    )

    assert result.wiki_induced_new_risk_expansion is False


def test_replay_rejects_safety_summary_not_bound_to_exact_prompt() -> None:
    recording = _valid_replay_recording(venue="kis")
    summary = dict(recording["legacy_safety_summary"])  # type: ignore[arg-type]
    summary["manager_input_hash"] = "f" * 64
    recording["legacy_safety_summary"] = summary

    result = replay_shadow_record(
        recording,
        lambda _prompt: {key: [] for key in _ACTION_KEYS_FOR_TEST},
    )

    assert result.comparison_status == "incomplete"
    assert "safety_summary_mismatch" in result.safety_gate_loss


def test_minimal_context_packet_is_not_a_complete_snapshot_trace() -> None:
    recording = _valid_replay_recording()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        recording["wiki_runtime_prompt_envelope"]
    )
    prompt = envelope.runtime_prompt()
    prompt["jue_wiki"]["jue_wiki_context_packet"] = {
        "snapshot_id": "snapshot:kis:1"
    }
    recording["wiki_runtime_prompt_envelope"] = (
        WikiRuntimePromptEnvelopeV1.from_runtime_prompt(
            venue="kis",
            legacy_manager_input=recording["manager_input"],  # type: ignore[arg-type]
            runtime_prompt=prompt,
        ).to_dict()
    )

    result = replay_shadow_record(
        recording,
        lambda _prompt: {key: [] for key in _ACTION_KEYS_FOR_TEST},
    )

    assert result.snapshot_trace_complete is False
    assert result.comparison_status == "incomplete"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "wrong"),
        ("compiler_version", "wrong"),
        ("claim_type", "rumor"),
        ("content_hash", "not-a-hash"),
        ("relationship_type", "mentions"),
    ],
)
def test_nested_packet_rejects_malformed_production_literals(
    field: str,
    value: str,
) -> None:
    recording = _valid_replay_recording()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        recording["wiki_runtime_prompt_envelope"]
    )
    prompt = envelope.runtime_prompt()
    page = _valid_selected_page()
    if field in page:
        page[field] = value
    elif field in page["claims"][0]:
        page["claims"][0][field] = value
    elif field in page["claims"][0]["evidence"][0]:
        page["claims"][0]["evidence"][0][field] = value
    else:
        page["relationships"][0][field] = value
    prompt["jue_wiki"]["jue_wiki_context_packet"]["selected_pages"] = [page]
    recording["wiki_runtime_prompt_envelope"] = (
        WikiRuntimePromptEnvelopeV1.from_runtime_prompt(
            venue="kis",
            legacy_manager_input=recording["manager_input"],
            runtime_prompt=prompt,
        ).to_dict()
    )

    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    result = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    assert result.snapshot_trace_complete is False
    assert result.comparison_status == "incomplete"


@pytest.mark.parametrize("hash_origin", ["source", "derived", "normalized_payload"])
def test_nested_packet_accepts_supported_evidence_hash_origins(
    hash_origin: str,
) -> None:
    recording = _valid_replay_recording()
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        recording["wiki_runtime_prompt_envelope"]
    )
    prompt = envelope.runtime_prompt()
    prompt["jue_wiki"]["jue_wiki_context_packet"]["selected_pages"] = [
        _valid_selected_page(hash_origin=hash_origin)
    ]
    recording["wiki_runtime_prompt_envelope"] = (
        WikiRuntimePromptEnvelopeV1.from_runtime_prompt(
            venue="kis",
            legacy_manager_input=recording["manager_input"],
            runtime_prompt=prompt,
        ).to_dict()
    )

    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    result = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    assert result.snapshot_trace_complete is True
    assert result.comparison_status == "complete"


def test_future_comparison_timestamp_is_rejected() -> None:
    future = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="created_at_future"):
        replace(
            _complete_comparison(venue="kis", run_id="kis:future"),
            created_at=future,
        )


def test_complete_comparison_requires_verified_completion_provenance() -> None:
    comparison = _complete_comparison(venue="kis", run_id="kis:provenance-required")

    with pytest.raises(
        ValueError,
        match="wiki_shadow_complete_completion_provenance_required",
    ):
        replace(
            comparison,
            completion_provenance_hash="",
            completion_provenance_verified=False,
        )


def test_direct_forged_second_identity_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path)
    store.initialize()
    row = _stored_complete_comparison(store, venue="kis", run_id="kis:forged")
    store.record(row)
    with sqlite3.connect(db_path) as conn:
        values = list(
            conn.execute(
                "SELECT * FROM wiki_shadow_comparisons_v1 WHERE comparison_id = ?",
                (row.comparison_id,),
            ).fetchone()
        )
        values[0] = "wiki-shadow:forged-second-id"
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
            conn.execute(
                f"INSERT INTO wiki_shadow_comparisons_v1 VALUES ({','.join('?' for _ in values)})",
                values,
            )


def test_initialization_with_legacy_duplicate_identity_fails_without_deleting(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path)
    store.initialize()
    row = _stored_complete_comparison(
        store,
        venue="kis",
        run_id="kis:legacy-duplicate",
    )
    store.record(row)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER wiki_shadow_comparisons_v1_no_duplicate")
        conn.execute("DROP TRIGGER wiki_shadow_comparisons_v1_complete_provenance")
        conn.execute("DROP INDEX ux_wiki_shadow_venue_run")
        values = list(
            conn.execute(
                "SELECT * FROM wiki_shadow_comparisons_v1 WHERE comparison_id = ?",
                (row.comparison_id,),
            ).fetchone()
        )
        values[0] = "wiki-shadow:legacy-forged"
        conn.execute(
            f"INSERT INTO wiki_shadow_comparisons_v1 VALUES ({','.join('?' for _ in values)})",
            values,
        )

    with pytest.raises(sqlite3.IntegrityError):
        store.initialize()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_comparisons_v1"
        ).fetchone() == (2,)


def test_store_eligibility_fails_closed_on_timestamp_order_corruption(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    store = JueWikiShadowStore(db_path, completion_verifier=_TEST_SIGNER)
    store.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER wiki_shadow_eligibility_v1_no_update")
        conn.execute(
            """
            UPDATE wiki_shadow_eligibility_v1
            SET evaluated_through = '2026-07-11T00:00:01+00:00',
                updated_at = '2026-07-11T00:00:00+00:00'
            WHERE venue = 'kis'
            """
        )

    result = store.eligibility("kis")

    assert result["required_eligible"] is False
    assert "eligibility_signature_invalid" in result["blockers"]
    assert "eligibility_timestamp_order_invalid" in result["blockers"]


def test_ambiguous_candidate_reference_is_explicit_and_incomplete() -> None:
    recording = _valid_replay_recording(venue="binance")
    recording["manager_input"]["candidates"] = [  # type: ignore[index]
        {"candidate_id": "c1", "symbol": "BTCUSDT", "market": "spot", "side": "long"},
        {"candidate_id": "c2", "symbol": "BTCUSDT", "market": "futures", "side": "long"},
    ]
    recording["legacy_safety_summary"] = ManagerSafetySummaryV1.from_manager_input(
        venue="binance",
        manager_input=recording["manager_input"],  # type: ignore[arg-type]
    ).to_dict()
    _rebind_runtime_envelope(recording)

    result = replay_shadow_record(
        recording,
        lambda _prompt: {
            "create_blocks": [{"symbol": "BTCUSDT", "qty": 1}],
            "update_blocks": [], "close_blocks": [], "pause_blocks": [],
            "adopt_existing_blocks": [],
        },
    )

    assert result.comparison_status == "incomplete"
    assert any("candidate_ambiguous" in value for value in result.candidate_delta)


def test_explicit_candidate_side_or_market_contradiction_is_incomplete() -> None:
    recording = _valid_replay_recording(venue="binance")
    recording["manager_input"]["candidates"] = [  # type: ignore[index]
        {"candidate_id": "c1", "symbol": "BTCUSDT", "market": "spot", "side": "long"}
    ]
    recording["legacy_safety_summary"] = ManagerSafetySummaryV1.from_manager_input(
        venue="binance", manager_input=recording["manager_input"]  # type: ignore[arg-type]
    ).to_dict()
    _rebind_runtime_envelope(recording)

    response = {
            "create_blocks": [
                {
                    "candidate_id": "c1",
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "qty": 1,
                }
            ],
            "update_blocks": [], "close_blocks": [], "pause_blocks": [],
            "adopt_existing_blocks": [],
    }
    result = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    assert result.comparison_status == "incomplete"
    assert any("candidate_ref_contradiction" in value for value in result.candidate_delta)


def test_kis_source_shaped_strategy_candidate_does_not_require_top_candidates() -> None:
    recording = _valid_replay_recording(venue="kis")
    manager = recording["manager_input"]  # type: ignore[assignment]
    manager["decision_inputs"] = [
        value for value in manager["decision_inputs"] if value != "candidates"
    ]
    manager.pop("candidates")
    manager["strategy"] = {
        "aggressive_opportunities": [
            {"symbol": "005930", "market": "krx", "side": "long"}
        ]
    }
    recording["legacy_safety_summary"] = ManagerSafetySummaryV1.from_manager_input(
        venue="kis", manager_input=manager
    ).to_dict()
    _rebind_runtime_envelope(recording)

    response = {
            "create_blocks": [
                {
                    "symbol": "005930", "qty": 1,
                    "target_price": 110, "stop_price": 90,
                }
            ],
            "update_blocks": [], "close_blocks": [], "pause_blocks": [],
            "adopt_existing_blocks": [],
    }
    result = replay_shadow_record(
        recording,
        lambda _prompt: response,
        completion_provenance=_verified_completion_provenance(recording, response),
    )

    assert result.comparison_status == "complete"


_ACTION_KEYS_FOR_TEST = (
    "adopt_existing_blocks",
    "close_blocks",
    "create_blocks",
    "pause_blocks",
    "update_blocks",
)


def test_replay_rejects_invalid_venue_contract_after_one_completion() -> None:
    calls = 0

    def invalid_completion(_prompt: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"create_blocks": {"symbol": "BTCUSDT"}}

    with pytest.raises(ValueError, match="create_blocks_must_be_list"):
        recording = _valid_replay_recording(venue="binance")
        recording["run_id"] = "binance:1"
        replay_shadow_record(recording, invalid_completion)
    assert calls == 1


@pytest.mark.parametrize("venue", ["kis", "binance"])
def test_replay_calls_existing_venue_manager_contract(
    venue: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    if venue == "kis":
        original = shadow_module.sanitize_kis_manager_actions

        def wrapped(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(shadow_module, "sanitize_kis_manager_actions", wrapped)
    else:
        original = shadow_module.validate_binance_manager_actions

        def wrapped(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(shadow_module, "validate_binance_manager_actions", wrapped)

    recording = _valid_replay_recording(venue=venue)
    recording["run_id"] = f"{venue}:contract"
    replay_shadow_record(
        recording,
        lambda _prompt: {
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
            "adopt_existing_blocks": [],
            "safety_gates": [],
        },
    )

    assert calls == 1


def test_replay_module_does_not_import_execution_or_exchange_adapters() -> None:
    tree = ast.parse(
        (Path(__file__).parents[1] / "src/tradecraft/services/jue_wiki_shadow.py")
        .read_text(encoding="utf-8")
    )
    imported = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    ]
    assert not any(
        token in name
        for name in imported
        for token in ("block_trader", "executor", "broker", "exchange")
    )


def test_canonical_hash_is_stable_across_mapping_order() -> None:
    assert canonical_payload_hash({"b": 2, "a": [1]}) == canonical_payload_hash(
        {"a": [1], "b": 2}
    )


def _recording_payload() -> dict[str, object]:
    recording = _shadow_recording(index=77).export_payload()
    response = {
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
        "adopt_existing_blocks": [],
        "safety_gates": [],
    }
    recording["recorded_completion"] = {
        "response": response,
        "provenance": _verified_completion_provenance(
            recording,
            response,
        ).to_dict(),
    }
    return recording


def test_replay_cli_defaults_to_dry_run_and_writes_no_database(
    tmp_path: Path,
) -> None:
    recording_path = tmp_path / "recording.json"
    recording_path.write_text(json.dumps(_recording_payload()), encoding="utf-8")
    output_path = tmp_path / "shadow.db"

    assert replay_cli_main(
        [
            "--venue",
            "kis",
            "--recording",
            str(recording_path),
            "--output",
            str(output_path),
        ]
    ) == 0
    assert not output_path.exists()


def test_replay_cli_consumes_actual_recording_database_row(tmp_path: Path) -> None:
    db_path = tmp_path / "wiki.db"
    recorder = build_runtime_recording_recorder(
        db_path, completion_verifier=_TEST_SIGNER
    )
    recorder(_shadow_recording(index=7))
    completion_path = tmp_path / "completion.json"
    completion_path.write_text(
        json.dumps({key: [] for key in _ACTION_KEYS_FOR_TEST}),
        encoding="utf-8",
    )
    output_path = tmp_path / "comparison.db"

    assert replay_cli_main(
        [
            "--venue", "kis",
            "--recording-db", str(db_path),
            "--manager-run-id", "7",
            "--completion", str(completion_path),
            "--output", str(output_path),
        ]
    ) == 0
    assert not output_path.exists()


def test_replay_cli_appends_verified_comparisons_to_authoritative_database(
    tmp_path: Path,
) -> None:
    recording_db = tmp_path / "recordings.db"
    recorder = build_runtime_recording_recorder(
        recording_db, completion_verifier=_TEST_SIGNER
    )
    recordings = [_shadow_recording(index=index) for index in (101, 102)]
    for recording in recordings:
        recorder(recording)
    output_path = recording_db

    for expected_count, recording in enumerate(recordings, start=1):
        response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
        exported = recording.export_payload()
        completion_path = tmp_path / f"completion-{expected_count}.json"
        completion_path.write_text(
            json.dumps(
                {
                    "response": response,
                    "provenance": _verified_completion_provenance(
                        exported,
                        response,
                    ).to_dict(),
                }
            ),
            encoding="utf-8",
        )

        assert replay_cli_main(
            [
                "--venue", "kis",
                "--recording-db", str(recording_db),
                "--run-id", recording.run_id,
                "--completion", str(completion_path),
                "--provenance-key", str(_TEST_SIGNER.key_path),
                "--output", str(output_path),
                "--no-dry-run",
            ]
        ) == 0

        with sqlite3.connect(output_path) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM wiki_shadow_comparisons_v1"
            ).fetchone() == (expected_count,)
        assert output_path.stat().st_mode & 0o777 == 0o600
        assert output_path.stat().st_nlink == 1

    assert JueWikiShadowStore(output_path).eligibility("kis")[
        "complete_sample_count"
    ] == 2


def test_replay_append_is_sqlite_atomic_under_forced_process_exit(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "shadow.db"
    signer = _TEST_SIGNER
    store = JueWikiShadowStore(output_path, completion_verifier=signer)
    store.initialize()
    baseline = _stored_complete_comparison(
        store,
        venue="kis",
        run_id="kis:crash:baseline",
    )
    store.record(baseline)
    pending = _stored_complete_comparison(
        store,
        venue="kis",
        run_id="kis:crash:pending",
    )

    process = multiprocessing.get_context("spawn").Process(
        target=_force_exit_after_sqlite_commit,
        args=(str(output_path), str(signer.key_path), pending),
    )
    process.start()
    process.join(timeout=15)

    assert process.exitcode == 91
    JueWikiShadowStore(
        output_path,
        completion_verifier=signer,
    ).initialize()
    with sqlite3.connect(output_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_comparisons_v1"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT complete_sample_count FROM wiki_shadow_eligibility_v1 "
            "WHERE venue = 'kis'"
        ).fetchone() == (1,)
    assert not output_path.with_name(output_path.name + "-journal").exists()


def test_cli_copy_on_write_never_mutates_forced_rename_victim(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "shadow.db"
    store = JueWikiShadowStore(output_path, completion_verifier=_TEST_SIGNER)
    store.initialize()
    baseline = _stored_complete_comparison(
        store, venue="kis", run_id="kis:cow:baseline"
    )
    store.record(baseline)
    pending = _stored_complete_comparison(
        store, venue="kis", run_id="kis:cow:pending"
    )
    before = output_path.read_bytes()
    victim_path = tmp_path / "victim.db"

    def replace_target() -> None:
        os.replace(output_path, victim_path)
        output_path.write_bytes(b"replacement-target")
        output_path.chmod(0o600)

    with pytest.raises(ValueError, match="inode_changed"):
        _REPLAY_MODULE._append_comparison_database(
            output_path,
            pending,
            completion_verifier=_TEST_SIGNER,
            _before_stage_connect=replace_target,
        )

    assert victim_path.read_bytes() == before
    with sqlite3.connect(victim_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_comparisons_v1"
        ).fetchone() == (1,)


def test_manager_writer_and_cli_copy_on_write_share_lock_without_lost_rows(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "shadow.db"
    store = JueWikiShadowStore(output_path, completion_verifier=_TEST_SIGNER)
    store.initialize()
    baseline = _stored_complete_comparison(
        store, venue="kis", run_id="kis:lock:baseline"
    )
    store.record(baseline)
    pending = _stored_complete_comparison(
        store, venue="kis", run_id="kis:lock:pending"
    )
    manager_recording = _shadow_recording(index=88_888)

    with ThreadPoolExecutor(max_workers=2) as pool:
        manager_future = pool.submit(
            JueWikiShadowStore(output_path).record_shadow_recording,
            manager_recording,
        )
        cli_future = pool.submit(
            _REPLAY_MODULE._append_comparison_database,
            output_path,
            pending,
            completion_verifier=_TEST_SIGNER,
        )
        assert manager_future.result(timeout=15) == manager_recording.recording_id
        assert cli_future.result(timeout=15) == pending.comparison_id

    reader = JueWikiShadowStore(
        output_path,
        completion_verifier=_TEST_SIGNER,
    )
    assert reader.recording("kis", run_id=manager_recording.run_id) == manager_recording
    eligibility = reader.eligibility("kis")
    assert "eligibility_signature_invalid" not in eligibility["blockers"]
    assert eligibility["complete_sample_count"] == 2
    with sqlite3.connect(output_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_comparisons_v1"
        ).fetchone() == (2,)


@pytest.mark.slow
def test_500_distinct_recordings_require_verified_hypothetical_completions(
    tmp_path: Path,
) -> None:
    store = JueWikiShadowStore(
        tmp_path / "jue_wiki_shadow.db",
        completion_verifier=_TEST_SIGNER,
    )
    store.initialize()
    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}

    for index in range(500):
        recording = _shadow_recording(index=1000 + index)
        store.record_shadow_recording(recording)
        exported = recording.export_payload()
        comparison = replay_shadow_record(
            exported,
            lambda _prompt, payload=response: payload,
            completion_provenance=_provider_completed_provenance(
                exported,
                response,
                request_id=f"request:operational:{index}:{uuid4().hex}",
            ),
            completion_verifier=_TEST_SIGNER,
        )
        assert comparison.comparison_status == "complete"
        store.record(comparison)

    eligible = store.eligibility("kis")
    assert eligible["complete_sample_count"] == 500
    assert eligible["required_eligible"] is True

    bad_recording = _shadow_recording(index=1500)
    store.record_shadow_recording(bad_recording)
    bad_exported = bad_recording.export_payload()
    bad_provenance = replace(
        _verified_completion_provenance(bad_exported, response),
        response_hash="f" * 64,
    )
    bad_comparison = replay_shadow_record(
        bad_exported,
        lambda _prompt: response,
        completion_provenance=bad_provenance,
        completion_verifier=_TEST_SIGNER,
    )
    assert bad_comparison.comparison_status == "incomplete"
    store.record(bad_comparison)
    assert store.eligibility("kis")["complete_sample_count"] == 500


@pytest.mark.slow
def test_recording_retention_recomputes_eligibility_from_active_sources(
    tmp_path: Path,
) -> None:
    store = JueWikiShadowStore(
        tmp_path / "jue_wiki_shadow.db",
        max_rows_per_venue=600,
        completion_verifier=_TEST_SIGNER,
    )
    store.initialize()
    response = {key: [] for key in _ACTION_KEYS_FOR_TEST}
    for index in range(500):
        recording = _shadow_recording(index=20_000 + index)
        store.record_shadow_recording(recording)
        exported = recording.export_payload()
        store.record(
            replay_shadow_record(
                exported,
                lambda _prompt, payload=response: payload,
                completion_provenance=_provider_completed_provenance(
                    exported,
                    response,
                    request_id=f"request:retention:{index}:{uuid4().hex}",
                ),
                completion_verifier=_TEST_SIGNER,
            )
        )
    assert store.eligibility("kis")["required_eligible"] is True

    for index in range(600):
        store.record_shadow_recording(_shadow_recording(index=30_000 + index))

    eligibility = store.eligibility("kis")
    assert eligibility["complete_sample_count"] == 0
    assert eligibility["required_eligible"] is False
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_shadow_comparisons_v1"
        ).fetchone() == (500,)


def test_replay_cli_rejects_runtime_output_and_venue_mismatch(
    tmp_path: Path,
) -> None:
    recording_path = tmp_path / "recording.json"
    recording_path.write_text(json.dumps(_recording_payload()), encoding="utf-8")
    runtime_output = tmp_path / ".runtime" / "shadow.db"

    with pytest.raises(ValueError, match="live_runtime_output_forbidden"):
        replay_cli_main(
            [
                "--venue",
                "kis",
                "--recording",
                str(recording_path),
                "--output",
                str(runtime_output),
            ]
        )
    with pytest.raises(ValueError, match="recording_venue_mismatch"):
        replay_cli_main(
            [
                "--venue",
                "binance",
                "--recording",
                str(recording_path),
                "--output",
                str(tmp_path / "other.db"),
            ]
        )


def test_replay_cli_explicit_write_records_outside_runtime(tmp_path: Path) -> None:
    recording_path = tmp_path / "recording.json"
    recording_path.write_text(json.dumps(_recording_payload()), encoding="utf-8")
    output_path = tmp_path / "shadow.db"

    assert replay_cli_main(
        [
            "--venue",
            "kis",
            "--recording",
            str(recording_path),
            "--output",
            str(output_path),
            "--provenance-key",
            str(_TEST_SIGNER.key_path),
            "--no-dry-run",
        ]
    ) == 0
    with sqlite3.connect(output_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wiki_shadow_comparisons_v1").fetchone() == (1,)


def test_replay_cli_reads_live_runtime_input_without_modifying_it(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    recording_path = runtime / "recording.json"
    recording_path.write_text(json.dumps(_recording_payload()), encoding="utf-8")
    before_bytes = recording_path.read_bytes()
    before_hash = sha256(before_bytes).hexdigest()
    before_mtime = recording_path.stat().st_mtime_ns

    replay_cli_main(
        [
            "--venue",
            "kis",
            "--recording",
            str(recording_path),
            "--output",
            str(tmp_path / "outside.db"),
        ]
    )

    assert sha256(recording_path.read_bytes()).hexdigest() == before_hash
    assert recording_path.stat().st_mtime_ns == before_mtime
    assert not (tmp_path / "outside.db").exists()


def test_replay_cli_rejects_symlink_resolving_into_runtime(tmp_path: Path) -> None:
    recording_path = tmp_path / "recording.json"
    recording_path.write_text(json.dumps(_recording_payload()), encoding="utf-8")
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    linked_parent = tmp_path / "linked-output"
    linked_parent.symlink_to(runtime, target_is_directory=True)

    with pytest.raises(ValueError, match="live_runtime_output_forbidden"):
        replay_cli_main(
            [
                "--venue",
                "kis",
                "--recording",
                str(recording_path),
                "--output",
                str(linked_parent / "shadow.db"),
            ]
        )


def test_replay_cli_rejects_existing_output_and_hardlink_alias(tmp_path: Path) -> None:
    recording_path = tmp_path / "recording.json"
    recording_path.write_text(json.dumps(_recording_payload()), encoding="utf-8")
    protected = tmp_path / ".runtime"
    protected.mkdir()
    protected_db = protected / "protected.db"
    protected_db.write_bytes(b"protected")
    alias = tmp_path / "alias.db"
    os.link(protected_db, alias)
    before = protected_db.read_bytes()

    with pytest.raises(ValueError, match="output_already_exists"):
        replay_cli_main(
            [
                "--venue",
                "kis",
                "--recording",
                str(recording_path),
                "--output",
                str(alias),
                "--no-dry-run",
            ]
        )

    assert protected_db.read_bytes() == before
    assert protected_db.stat().st_nlink == 2


def test_replay_cli_safely_installs_special_character_filename(tmp_path: Path) -> None:
    recording_path = tmp_path / "recording ? #.json"
    recording_path.write_text(json.dumps(_recording_payload()), encoding="utf-8")
    output_path = tmp_path / "shadow ? # db.sqlite"

    assert replay_cli_main(
        [
            "--venue",
            "kis",
            "--recording",
            str(recording_path),
            "--output",
            str(output_path),
            "--no-dry-run",
        ]
    ) == 0

    assert output_path.is_file()
    assert output_path.stat().st_nlink == 1
    assert JueWikiShadowStore(output_path).eligibility("kis")["venue"] == "kis"
