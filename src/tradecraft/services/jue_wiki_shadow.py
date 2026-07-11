from __future__ import annotations

import inspect
import fcntl
import hmac
import json
import os
import sqlite3
import threading
import secrets
import stat
import zlib
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Literal

from tradecraft.services.binance_manager_contract import (
    apply_manager_contract_aliases,
    normalize_manager_hold_decision as normalize_binance_manager_hold_decision,
    validate_manager_actions as validate_binance_manager_actions,
)
from tradecraft.services.binance_manager_prompt import (
    manager_response_contract_error as binance_manager_response_contract_error,
)
from tradecraft.services.jue_wiki_context import (
    strip_direct_raw_rag_context,
    wiki_eligibility_freshness_reason,
)
from tradecraft.services.jue_wiki_prompt_policy import (
    apply_jue_wiki_prompt_policy,
    extract_wiki_context_packet,
)
from tradecraft.services.jue_wiki_risk import (
    apply_binance_wiki_decision_gate,
    apply_kis_wiki_decision_gate,
    binance_update_adds_new_risk,
    kis_update_adds_new_risk,
)
from tradecraft.services.jue_wiki_contract import (
    EvidenceRefV1,
    JueWikiPageV3,
    WikiClaimV3,
    WikiRelationshipV1,
)
from tradecraft.services.kis_manager_actions import sanitize_kis_manager_actions
from tradecraft.services.kis_manager_prompt import (
    kis_manager_response_contract_error,
    sanitize_kis_hold_decision,
)
from tradecraft.services.binance_lane import normalize_binance_horizon
from tradecraft.services.binance_symbol import normalize_market, normalize_position_side


_VENUES = frozenset({"kis", "binance"})
_COMPARISON_STATUSES = frozenset({"complete", "incomplete", "error"})
_ACTION_KEYS = (
    "adopt_existing_blocks",
    "close_blocks",
    "create_blocks",
    "pause_blocks",
    "update_blocks",
)
_MIN_REQUIRED_SAMPLES = 500
_COMPARISON_VERSION = "wiki_shadow_comparison_v1"
_SAFETY_SUMMARY_VERSION = "manager_safety_summary_v1"
_RUNTIME_ENVELOPE_VERSION = "wiki_runtime_prompt_envelope_v1"
_SHADOW_RECORDING_VERSION = "wiki_shadow_recording_v1"
_SHADOW_RECORDING_MAX_ROWS_PER_VENUE = 600
_SHADOW_RECORDING_MAX_COMPRESSED_BYTES = 300_000
_SHADOW_RECORDING_MAX_UNCOMPRESSED_BYTES = 1_000_000


class _LockedConnection(sqlite3.Connection):
    _wiki_lock_fd: int = -1

    def _release_wiki_lock(self) -> None:
        if self._wiki_lock_fd >= 0:
            fcntl.flock(self._wiki_lock_fd, fcntl.LOCK_UN)
            os.close(self._wiki_lock_fd)
            self._wiki_lock_fd = -1

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._release_wiki_lock()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_payload_hash(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_instant(value: str) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("wiki_shadow_created_at_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("wiki_shadow_created_at_timezone_required")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ManagerSafetySummaryV1:
    venue: Literal["kis", "binance"]
    manager_input_hash: str
    blockers: tuple[str, ...]
    execution_allowed: bool
    new_risk_allowed: bool
    section_hashes: tuple[tuple[str, str], ...]
    version: str = _SAFETY_SUMMARY_VERSION

    def __post_init__(self) -> None:
        venue = str(self.venue or "").strip().lower()
        input_hash = str(self.manager_input_hash or "").strip().lower()
        if venue not in _VENUES:
            raise ValueError("manager_safety_summary_venue_invalid")
        if len(input_hash) != 64 or any(char not in "0123456789abcdef" for char in input_hash):
            raise ValueError("manager_safety_summary_input_hash_invalid")
        if self.version != _SAFETY_SUMMARY_VERSION:
            raise ValueError("manager_safety_summary_version_invalid")
        if type(self.execution_allowed) is not bool:
            raise ValueError("manager_safety_summary_execution_allowed_invalid")
        if type(self.new_risk_allowed) is not bool:
            raise ValueError("manager_safety_summary_new_risk_allowed_invalid")
        if not isinstance(self.blockers, tuple):
            raise ValueError("manager_safety_summary_blockers_invalid")
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "manager_input_hash", input_hash)
        object.__setattr__(
            self,
            "blockers",
            tuple(sorted({str(value).strip().lower() for value in self.blockers if str(value).strip()})),
        )
        if not isinstance(self.section_hashes, tuple):
            raise ValueError("manager_safety_summary_section_hashes_invalid")
        normalized_sections: list[tuple[str, str]] = []
        for row in self.section_hashes:
            if not isinstance(row, tuple) or len(row) != 2:
                raise ValueError("manager_safety_summary_section_hashes_invalid")
            name = str(row[0] or "").strip().lower()
            digest = str(row[1] or "").strip().lower()
            if not name or len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise ValueError("manager_safety_summary_section_hashes_invalid")
            normalized_sections.append((name, digest))
        if "execution_gate" not in {name for name, _digest in normalized_sections}:
            raise ValueError("manager_safety_summary_execution_gate_hash_missing")
        object.__setattr__(self, "section_hashes", tuple(sorted(set(normalized_sections))))

    @classmethod
    def from_manager_input(
        cls,
        *,
        venue: str,
        manager_input: dict[str, Any],
    ) -> ManagerSafetySummaryV1:
        blockers, execution_allowed, new_risk_allowed, valid = _local_safety_state(
            venue,
            manager_input,
        )
        if not valid:
            raise ValueError("manager_safety_summary_input_invalid")
        return cls(
            venue=venue,  # type: ignore[arg-type]
            manager_input_hash=canonical_payload_hash(manager_input),
            blockers=blockers,
            execution_allowed=execution_allowed,
            new_risk_allowed=new_risk_allowed,
            section_hashes=_safety_section_hashes(manager_input),
        )

    @classmethod
    def from_dict(cls, value: Any) -> ManagerSafetySummaryV1:
        if not isinstance(value, dict):
            raise ValueError("manager_safety_summary_invalid")
        blockers = value.get("blockers")
        section_hashes = value.get("section_hashes")
        if not isinstance(blockers, (list, tuple)):
            raise ValueError("manager_safety_summary_blockers_invalid")
        if not isinstance(section_hashes, (list, tuple)):
            raise ValueError("manager_safety_summary_section_hashes_invalid")
        return cls(
            venue=value.get("venue"),  # type: ignore[arg-type]
            manager_input_hash=value.get("manager_input_hash"),
            blockers=tuple(blockers),
            execution_allowed=value.get("execution_allowed"),
            new_risk_allowed=value.get("new_risk_allowed"),
            section_hashes=tuple(
                tuple(row) if isinstance(row, (list, tuple)) else ("", "")
                for row in section_hashes
            ),
            version=value.get("version"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WikiRuntimePromptEnvelopeV1:
    venue: Literal["kis", "binance"]
    legacy_manager_input_hash: str
    wiki_runtime_prompt_hash: str
    snapshot_trace_hash: str
    gate_hash: str
    runtime_prompt_json: str
    version: str = _RUNTIME_ENVELOPE_VERSION

    def __post_init__(self) -> None:
        venue = str(self.venue or "").strip().lower()
        if venue not in _VENUES:
            raise ValueError("wiki_runtime_envelope_venue_invalid")
        if self.version != _RUNTIME_ENVELOPE_VERSION:
            raise ValueError("wiki_runtime_envelope_version_invalid")
        object.__setattr__(self, "venue", venue)
        for field_name in (
            "legacy_manager_input_hash",
            "wiki_runtime_prompt_hash",
            "snapshot_trace_hash",
            "gate_hash",
        ):
            value = str(getattr(self, field_name) or "").strip().lower()
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"wiki_runtime_envelope_{field_name}_invalid")
            object.__setattr__(self, field_name, value)
        try:
            prompt = json.loads(self.runtime_prompt_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("wiki_runtime_envelope_prompt_json_invalid") from exc
        if not isinstance(prompt, dict):
            raise ValueError("wiki_runtime_envelope_prompt_invalid")
        canonical = _canonical_json(prompt)
        object.__setattr__(self, "runtime_prompt_json", canonical)
        if canonical_payload_hash(prompt) != self.wiki_runtime_prompt_hash:
            raise ValueError("wiki_runtime_envelope_prompt_hash_mismatch")
        wiki_section = prompt.get("jue_wiki")
        packet = extract_wiki_context_packet(wiki_section)
        gate = prompt.get("jue_wiki_decision_gate")
        decision_inputs = prompt.get("decision_inputs")
        if not isinstance(packet, dict) or "jue_wiki_context" in prompt:
            raise ValueError("wiki_runtime_envelope_wiki_shape_invalid")
        if not isinstance(prompt.get("jue_wiki_application"), dict):
            raise ValueError("wiki_runtime_envelope_application_missing")
        if not isinstance(gate, dict):
            raise ValueError("wiki_runtime_envelope_gate_missing")
        if (
            gate.get("version") != "wiki_decision_gate_v1"
            or type(gate.get("allow_new_risk")) is not bool
            or gate.get("allow_exit_actions") is not True
            or gate.get("read_mode") not in {"shadow", "prefer", "required"}
        ):
            raise ValueError("wiki_runtime_envelope_gate_invalid")
        if not isinstance(decision_inputs, list) or not {
            "jue_wiki",
            "jue_wiki_decision_gate",
        }.issubset(set(decision_inputs)):
            raise ValueError("wiki_runtime_envelope_decision_inputs_invalid")
        if canonical_payload_hash(packet) != self.snapshot_trace_hash:
            raise ValueError("wiki_runtime_envelope_snapshot_trace_hash_mismatch")
        if canonical_payload_hash(gate) != self.gate_hash:
            raise ValueError("wiki_runtime_envelope_gate_hash_mismatch")

    @classmethod
    def from_runtime_prompt(
        cls,
        *,
        venue: str,
        legacy_manager_input: dict[str, Any],
        runtime_prompt: dict[str, Any],
    ) -> WikiRuntimePromptEnvelopeV1:
        packet = extract_wiki_context_packet(runtime_prompt.get("jue_wiki"))
        gate = runtime_prompt.get("jue_wiki_decision_gate")
        if not isinstance(packet, dict) or not isinstance(gate, dict):
            raise ValueError("wiki_runtime_envelope_sections_missing")
        return cls(
            venue=venue,  # type: ignore[arg-type]
            legacy_manager_input_hash=canonical_payload_hash(legacy_manager_input),
            wiki_runtime_prompt_hash=canonical_payload_hash(runtime_prompt),
            snapshot_trace_hash=canonical_payload_hash(packet),
            gate_hash=canonical_payload_hash(gate),
            runtime_prompt_json=_canonical_json(runtime_prompt),
        )

    @classmethod
    def from_dict(cls, value: Any) -> WikiRuntimePromptEnvelopeV1:
        if not isinstance(value, dict):
            raise ValueError("wiki_runtime_envelope_invalid")
        return cls(
            venue=value.get("venue"),  # type: ignore[arg-type]
            legacy_manager_input_hash=value.get("legacy_manager_input_hash"),
            wiki_runtime_prompt_hash=value.get("wiki_runtime_prompt_hash"),
            snapshot_trace_hash=value.get("snapshot_trace_hash"),
            gate_hash=value.get("gate_hash"),
            runtime_prompt_json=value.get("runtime_prompt_json"),
            version=value.get("version"),
        )

    def runtime_prompt(self) -> dict[str, Any]:
        return json.loads(self.runtime_prompt_json)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WikiShadowRecordingV1:
    venue: Literal["kis", "binance"]
    run_id: str
    manager_run_id: str
    legacy_manager_input_json: str
    source_runtime_prompt_json: str
    final_actions_json: str
    safety_summary_json: str
    snapshot_id: str
    legacy_manager_input_hash: str
    source_runtime_prompt_hash: str
    final_actions_hash: str
    safety_summary_hash: str
    snapshot_trace_hash: str
    gate_hash: str
    wiki_suppression_count: int
    simulate_wiki_outage: bool
    created_at: str
    version: str = _SHADOW_RECORDING_VERSION

    def __post_init__(self) -> None:
        venue = str(self.venue or "").strip().lower()
        run_id = str(self.run_id or "").strip()
        manager_run_id = str(self.manager_run_id or "").strip()
        snapshot_id = str(self.snapshot_id or "").strip()
        if venue not in _VENUES:
            raise ValueError("wiki_shadow_recording_venue_invalid")
        if not run_id or not manager_run_id:
            raise ValueError("wiki_shadow_recording_identity_invalid")
        if self.version != _SHADOW_RECORDING_VERSION:
            raise ValueError("wiki_shadow_recording_version_invalid")
        if type(self.simulate_wiki_outage) is not bool:
            raise ValueError("wiki_shadow_recording_outage_must_be_bool")
        if type(self.wiki_suppression_count) is not int or self.wiki_suppression_count != 0:
            raise ValueError("wiki_shadow_recording_wiki_not_noop")
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "manager_run_id", manager_run_id)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "created_at", _utc_instant(self.created_at).isoformat())
        if _utc_instant(self.created_at) > datetime.now(timezone.utc):
            raise ValueError("wiki_shadow_recording_created_at_future")
        payloads = (
            ("legacy_manager_input_json", "legacy_manager_input_hash"),
            ("source_runtime_prompt_json", "source_runtime_prompt_hash"),
            ("final_actions_json", "final_actions_hash"),
            ("safety_summary_json", "safety_summary_hash"),
        )
        parsed: dict[str, Any] = {}
        for json_field, hash_field in payloads:
            try:
                value = json.loads(getattr(self, json_field))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"wiki_shadow_recording_{json_field}_invalid") from exc
            if not isinstance(value, dict):
                raise ValueError(f"wiki_shadow_recording_{json_field}_invalid")
            canonical = _canonical_json(value)
            object.__setattr__(self, json_field, canonical)
            expected_hash = canonical_payload_hash(value)
            if str(getattr(self, hash_field) or "").strip().lower() != expected_hash:
                raise ValueError(f"wiki_shadow_recording_{hash_field}_mismatch")
            object.__setattr__(self, hash_field, expected_hash)
            parsed[json_field] = value
        prompt = parsed["source_runtime_prompt_json"]
        packet = extract_wiki_context_packet(prompt.get("jue_wiki"))
        gate = prompt.get("jue_wiki_decision_gate")
        if not isinstance(packet, dict) or not isinstance(gate, dict):
            raise ValueError("wiki_shadow_recording_wiki_sections_missing")
        if gate.get("read_mode") not in {"shadow", "prefer"}:
            raise ValueError("wiki_shadow_recording_source_not_advisory")
        if gate.get("allow_new_risk") is not True:
            raise ValueError("wiki_shadow_recording_source_gate_not_noop")
        if str(packet.get("snapshot_id") or "").strip() != snapshot_id:
            raise ValueError("wiki_shadow_recording_snapshot_mismatch")
        if canonical_payload_hash(packet) != str(self.snapshot_trace_hash).lower():
            raise ValueError("wiki_shadow_recording_snapshot_trace_hash_mismatch")
        if canonical_payload_hash(gate) != str(self.gate_hash).lower():
            raise ValueError("wiki_shadow_recording_gate_hash_mismatch")
        ManagerSafetySummaryV1.from_dict(parsed["safety_summary_json"])
        safety_summary = ManagerSafetySummaryV1.from_dict(
            parsed["safety_summary_json"]
        )
        if (
            safety_summary.venue != venue
            or safety_summary.manager_input_hash != self.legacy_manager_input_hash
        ):
            raise ValueError("wiki_shadow_recording_safety_summary_unbound")
        actions = parsed["final_actions_json"]
        if not set(_ACTION_KEYS).issubset(actions):
            raise ValueError("wiki_shadow_recording_final_actions_incomplete")
        _validate_action_shapes(actions)
        decision_inputs = prompt.get("decision_inputs")
        if not isinstance(prompt.get("jue_wiki_application"), dict) or not isinstance(
            decision_inputs, list
        ) or not {"jue_wiki", "jue_wiki_decision_gate"}.issubset(
            set(decision_inputs)
        ):
            raise ValueError("wiki_shadow_recording_prompt_contract_invalid")

    @property
    def recording_id(self) -> str:
        return "wiki-recording:" + canonical_payload_hash(
            {
                "venue": self.venue,
                "run_id": self.run_id,
                "manager_run_id": self.manager_run_id,
            }
        )

    @classmethod
    def from_run(
        cls,
        *,
        venue: str,
        run_id: str,
        manager_run_id: str | int,
        legacy_manager_input: dict[str, Any],
        source_runtime_prompt: dict[str, Any],
        final_actions: dict[str, Any],
        wiki_suppression_count: int = 0,
        simulate_wiki_outage: bool = True,
        created_at: str | None = None,
    ) -> WikiShadowRecordingV1:
        if type(wiki_suppression_count) is not int or wiki_suppression_count != 0:
            raise ValueError("wiki_shadow_recording_wiki_not_noop")
        wiki_section = source_runtime_prompt.get("jue_wiki")
        packet = extract_wiki_context_packet(wiki_section)
        gate = source_runtime_prompt.get("jue_wiki_decision_gate")
        if not isinstance(packet, dict) or not isinstance(gate, dict):
            raise ValueError("wiki_shadow_recording_wiki_sections_missing")
        summary = ManagerSafetySummaryV1.from_manager_input(
            venue=venue,
            manager_input=legacy_manager_input,
        )
        return cls(
            venue=venue,  # type: ignore[arg-type]
            run_id=str(run_id),
            manager_run_id=str(manager_run_id),
            legacy_manager_input_json=_canonical_json(legacy_manager_input),
            source_runtime_prompt_json=_canonical_json(source_runtime_prompt),
            final_actions_json=_canonical_json(final_actions),
            safety_summary_json=_canonical_json(summary.to_dict()),
            snapshot_id=str(packet.get("snapshot_id") or ""),
            legacy_manager_input_hash=canonical_payload_hash(legacy_manager_input),
            source_runtime_prompt_hash=canonical_payload_hash(source_runtime_prompt),
            final_actions_hash=canonical_payload_hash(final_actions),
            safety_summary_hash=canonical_payload_hash(summary.to_dict()),
            snapshot_trace_hash=canonical_payload_hash(packet),
            gate_hash=canonical_payload_hash(gate),
            wiki_suppression_count=0,
            simulate_wiki_outage=simulate_wiki_outage,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def export_payload(self) -> dict[str, Any]:
        prompt = json.loads(self.source_runtime_prompt_json)
        envelope = WikiRuntimePromptEnvelopeV1.from_runtime_prompt(
            venue=self.venue,
            legacy_manager_input=json.loads(self.legacy_manager_input_json),
            runtime_prompt=prompt,
        )
        return {
            "recording_id": self.recording_id,
            "recording_created_at": self.created_at,
            "run_id": self.run_id,
            "manager_run_id": self.manager_run_id,
            "venue": self.venue,
            "manager_input": json.loads(self.legacy_manager_input_json),
            "legacy_actions": json.loads(self.final_actions_json),
            "legacy_safety_summary": json.loads(self.safety_summary_json),
            "wiki_snapshot_id": self.snapshot_id,
            "wiki_snapshot_trace_hash": self.snapshot_trace_hash,
            "wiki_runtime_prompt_envelope": envelope.to_dict(),
            "simulate_wiki_outage": self.simulate_wiki_outage,
        }


@dataclass(frozen=True, slots=True)
class WikiCompletionProvenanceV1:
    recording_id: str
    source_prompt_hash: str
    target_prompt_hash: str
    response_hash: str
    provider: str
    model: str
    request_id: str
    completed_at: str
    verified: bool
    key_id: str = ""
    signature: str = ""
    version: str = "wiki_completion_provenance_v1"

    def __post_init__(self) -> None:
        if self.version != "wiki_completion_provenance_v1":
            raise ValueError("wiki_completion_provenance_version_invalid")
        for field_name in ("recording_id", "provider", "model", "request_id"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"wiki_completion_provenance_{field_name}_required")
            object.__setattr__(self, field_name, value)
        for field_name in (
            "source_prompt_hash",
            "target_prompt_hash",
            "response_hash",
        ):
            value = str(getattr(self, field_name) or "").strip().lower()
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError(f"wiki_completion_provenance_{field_name}_invalid")
            object.__setattr__(self, field_name, value)
        if type(self.verified) is not bool:
            raise ValueError("wiki_completion_provenance_verified_must_be_bool")
        completed_at = _utc_instant(self.completed_at)
        if completed_at > datetime.now(timezone.utc):
            raise ValueError("wiki_completion_provenance_completed_at_future")
        object.__setattr__(self, "completed_at", completed_at.isoformat())

    @classmethod
    def from_dict(cls, value: Any) -> WikiCompletionProvenanceV1:
        if not isinstance(value, dict):
            raise ValueError("wiki_completion_provenance_invalid")
        return cls(**value)

    @property
    def provenance_hash(self) -> str:
        return canonical_payload_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WikiCompletionSigner:
    def __init__(self, key_path: str | Path) -> None:
        self.key_path = Path(key_path)
        if not self.key_path.is_absolute():
            raise ValueError("wiki_completion_key_path_must_be_absolute")
        if ".runtime" in self.key_path.resolve(strict=False).parts:
            raise ValueError("wiki_completion_key_path_runtime_forbidden")
        self._key = self._load_or_create_key()
        self.key_id = sha256(self._key).hexdigest()[:24]

    def _load_or_create_key(self) -> bytes:
        self.key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_stat = self.key_path.parent.stat()
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.getuid()
            or stat.S_IMODE(parent_stat.st_mode) & 0o022
        ):
            raise ValueError("wiki_completion_key_parent_unsafe")
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(
                self.key_path,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            descriptor = os.open(self.key_path, flags)
            created = False
        else:
            created = True
        try:
            file_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != os.getuid()
                or file_stat.st_nlink != 1
                or (file_stat.st_mode & 0o777) != 0o600
            ):
                raise ValueError("wiki_completion_key_file_unsafe")
            if created:
                key = secrets.token_bytes(32)
                os.write(descriptor, key)
                os.fsync(descriptor)
                return key
            key = os.read(descriptor, 33)
            if len(key) != 32:
                raise ValueError("wiki_completion_key_size_invalid")
            return key
        finally:
            os.close(descriptor)

    @staticmethod
    def _unsigned_payload(provenance: WikiCompletionProvenanceV1) -> dict[str, Any]:
        payload = provenance.to_dict()
        payload["signature"] = ""
        return payload

    def _signature(self, provenance: WikiCompletionProvenanceV1) -> str:
        return hmac.new(
            self._key,
            _canonical_json(self._unsigned_payload(provenance)).encode("utf-8"),
            "sha256",
        ).hexdigest()

    def sign_payload(self, purpose: str, payload: dict[str, Any]) -> str:
        message = f"{purpose}\0{_canonical_json(payload)}".encode("utf-8")
        return hmac.new(self._key, message, "sha256").hexdigest()

    def verify_payload(
        self,
        purpose: str,
        payload: dict[str, Any],
        signature: str,
    ) -> bool:
        return hmac.compare_digest(
            str(signature or ""),
            self.sign_payload(purpose, payload),
        )

    def complete(
        self,
        *,
        recording: dict[str, Any],
        target_prompt: dict[str, Any],
        complete_json: Callable[[dict[str, Any]], dict[str, Any]],
        provider: str,
        model: str,
        request_id: str,
    ) -> tuple[dict[str, Any], WikiCompletionProvenanceV1]:
        response = complete_json(target_prompt)
        if inspect.isawaitable(response) or not isinstance(response, dict):
            raise ValueError("wiki_shadow_completion_must_be_object")
        provenance = self.sign(
            recording=recording,
            target_prompt=target_prompt,
            response=response,
            provider=provider,
            model=model,
            request_id=request_id,
        )
        return response, provenance

    def sign(
        self,
        *,
        recording: dict[str, Any],
        target_prompt: dict[str, Any],
        response: dict[str, Any],
        provider: str,
        model: str,
        request_id: str,
        completed_at: str | None = None,
    ) -> WikiCompletionProvenanceV1:
        envelope = WikiRuntimePromptEnvelopeV1.from_dict(
            recording.get("wiki_runtime_prompt_envelope")
        )
        provenance = WikiCompletionProvenanceV1(
            recording_id=str(recording.get("recording_id") or ""),
            source_prompt_hash=envelope.wiki_runtime_prompt_hash,
            target_prompt_hash=canonical_payload_hash(target_prompt),
            response_hash=canonical_payload_hash(response),
            provider=provider,
            model=model,
            request_id=request_id,
            completed_at=completed_at or datetime.now(timezone.utc).isoformat(),
            verified=True,
            key_id=self.key_id,
            signature="",
        )
        return replace(
            provenance,
            signature=self._signature(provenance),
        )

    def verify(
        self,
        provenance: WikiCompletionProvenanceV1,
        *,
        recording: dict[str, Any],
        target_prompt: dict[str, Any],
        response: dict[str, Any],
    ) -> bool:
        try:
            envelope = WikiRuntimePromptEnvelopeV1.from_dict(
                recording.get("wiki_runtime_prompt_envelope")
            )
        except ValueError:
            return False
        recording_created_at = str(recording.get("recording_created_at") or "")
        try:
            completion_time = _utc_instant(provenance.completed_at)
            recording_time = _utc_instant(recording_created_at)
        except ValueError:
            return False
        return bool(
            provenance.verified is True
            and provenance.key_id == self.key_id
            and provenance.recording_id == str(recording.get("recording_id") or "")
            and provenance.source_prompt_hash == envelope.wiki_runtime_prompt_hash
            and provenance.target_prompt_hash == canonical_payload_hash(target_prompt)
            and provenance.response_hash == canonical_payload_hash(response)
            and recording_time <= completion_time <= datetime.now(timezone.utc)
            and hmac.compare_digest(provenance.signature, self._signature(provenance))
        )

    def verify_hashes(
        self,
        provenance: WikiCompletionProvenanceV1,
        *,
        recording_id: str,
        source_prompt_hash: str,
        target_prompt_hash: str,
        response_hash: str,
        recording_created_at: str,
    ) -> bool:
        try:
            completion_time = _utc_instant(provenance.completed_at)
            recording_time = _utc_instant(recording_created_at)
        except ValueError:
            return False
        return bool(
            provenance.verified is True
            and provenance.key_id == self.key_id
            and provenance.recording_id == recording_id
            and provenance.source_prompt_hash == source_prompt_hash
            and provenance.target_prompt_hash == target_prompt_hash
            and provenance.response_hash == response_hash
            and recording_time <= completion_time <= datetime.now(timezone.utc)
            and hmac.compare_digest(provenance.signature, self._signature(provenance))
        )


@dataclass(frozen=True, slots=True)
class WikiShadowComparisonV1:
    run_id: str
    venue: Literal["kis", "binance"]
    legacy_prompt_hash: str
    wiki_prompt_hash: str
    snapshot_id: str
    legacy_action_hash: str
    wiki_action_hash: str
    safety_gate_loss: tuple[str, ...]
    direct_raw_rag_paths: tuple[str, ...]
    comparison_status: Literal["complete", "incomplete", "error"]
    created_at: str
    snapshot_trace_complete: bool = True
    wiki_induced_new_risk_expansion: bool = False
    simulated_wiki_outage: bool = True
    wiki_read_mode: Literal["shadow", "prefer", "required"] = "required"
    candidate_delta: tuple[str, ...] = ()
    action_delta: tuple[str, ...] = ()
    safety_gate_delta: tuple[str, ...] = ()
    removed_raw_rag_paths: tuple[str, ...] = ()
    legacy_safety_summary_hash: str = ""
    wiki_safety_summary_hash: str = ""
    recording_id: str = ""
    completion_provenance_json: str = ""
    completion_provenance_hash: str = ""
    completion_provenance_verified: bool = False
    version: str = "wiki_shadow_comparison_v1"

    def __post_init__(self) -> None:
        run_id = str(self.run_id or "").strip()
        venue = str(self.venue or "").strip().lower()
        snapshot_id = str(self.snapshot_id or "").strip()
        status = str(self.comparison_status or "").strip().lower()
        read_mode = str(self.wiki_read_mode or "").strip().lower()
        if not run_id:
            raise ValueError("wiki_shadow_run_id_required")
        if venue not in _VENUES:
            raise ValueError("wiki_shadow_venue_invalid")
        if status not in _COMPARISON_STATUSES:
            raise ValueError("wiki_shadow_comparison_status_invalid")
        if read_mode not in {"shadow", "prefer", "required"}:
            raise ValueError("wiki_shadow_read_mode_invalid")
        if self.version != _COMPARISON_VERSION:
            raise ValueError("wiki_shadow_version_invalid")
        if status == "complete" and not snapshot_id:
            raise ValueError("wiki_shadow_snapshot_id_required")
        for field_name in (
            "snapshot_trace_complete",
            "wiki_induced_new_risk_expansion",
            "simulated_wiki_outage",
            "completion_provenance_verified",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"wiki_shadow_{field_name}_must_be_bool")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "comparison_status", status)
        object.__setattr__(self, "wiki_read_mode", read_mode)
        object.__setattr__(self, "created_at", _utc_instant(self.created_at).isoformat())
        if _utc_instant(self.created_at) > datetime.now(timezone.utc):
            raise ValueError("wiki_shadow_created_at_future")
        for field_name in (
            "legacy_prompt_hash",
            "wiki_prompt_hash",
            "legacy_action_hash",
            "wiki_action_hash",
        ):
            value = str(getattr(self, field_name) or "").strip().lower()
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"wiki_shadow_{field_name}_invalid")
            object.__setattr__(self, field_name, value)
        provenance_hash = str(self.completion_provenance_hash or "").strip().lower()
        if provenance_hash and (
            len(provenance_hash) != 64
            or any(character not in "0123456789abcdef" for character in provenance_hash)
        ):
            raise ValueError("wiki_shadow_completion_provenance_hash_invalid")
        object.__setattr__(self, "completion_provenance_hash", provenance_hash)
        recording_id = str(self.recording_id or "").strip()
        object.__setattr__(self, "recording_id", recording_id)
        provenance_json = str(self.completion_provenance_json or "").strip()
        if provenance_json:
            try:
                provenance_payload = json.loads(provenance_json)
            except json.JSONDecodeError as exc:
                raise ValueError("wiki_shadow_completion_provenance_json_invalid") from exc
            provenance_json = _canonical_json(provenance_payload)
        object.__setattr__(self, "completion_provenance_json", provenance_json)
        if status == "complete" and (
            self.completion_provenance_verified is not True or not provenance_hash
        ):
            raise ValueError("wiki_shadow_complete_completion_provenance_required")
        for field_name in (
            "safety_gate_loss",
            "direct_raw_rag_paths",
            "candidate_delta",
            "action_delta",
            "safety_gate_delta",
            "removed_raw_rag_paths",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, tuple):
                raise ValueError(f"wiki_shadow_{field_name}_must_be_tuple")
            object.__setattr__(
                self,
                field_name,
                tuple(
                    sorted(
                        {
                            str(item).strip().lower()
                            for item in value
                            if str(item).strip()
                        }
                    )
                ),
            )
        for field_name in (
            "legacy_safety_summary_hash",
            "wiki_safety_summary_hash",
        ):
            value = str(getattr(self, field_name) or "").strip().lower()
            if value and (
                len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"wiki_shadow_{field_name}_invalid")
            if status == "complete" and not value:
                raise ValueError(f"wiki_shadow_{field_name}_required")
            object.__setattr__(self, field_name, value)

    @property
    def comparison_id(self) -> str:
        identity_hash = canonical_payload_hash(
            {"run_id": self.run_id, "venue": self.venue}
        )
        return f"wiki-shadow:{identity_hash}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JueWikiShadowStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        timeout_seconds: float = 0.5,
        max_rows_per_venue: int | None = None,
        max_compressed_bytes: int | None = None,
        max_uncompressed_bytes: int | None = None,
        completion_verifier: WikiCompletionSigner | None = None,
        _write_uri: str = "",
        _after_comparison_insert: Callable[[], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.is_absolute():
            raise ValueError("wiki_shadow_db_path_must_be_absolute")
        if ".runtime" in self.db_path.resolve(strict=False).parts:
            raise ValueError("wiki_shadow_db_path_runtime_forbidden")
        try:
            db_stat = self.db_path.lstat()
        except FileNotFoundError:
            pass
        else:
            if (
                not stat.S_ISREG(db_stat.st_mode)
                or db_stat.st_uid != os.getuid()
                or db_stat.st_nlink != 1
            ):
                raise ValueError("wiki_shadow_db_file_unsafe")
        self.timeout_seconds = max(min(float(timeout_seconds), 5.0), 0.05)
        self._explicit_max_rows = max_rows_per_venue is not None
        self._explicit_max_compressed = max_compressed_bytes is not None
        self._explicit_max_uncompressed = max_uncompressed_bytes is not None
        max_rows_per_venue = (
            _SHADOW_RECORDING_MAX_ROWS_PER_VENUE
            if max_rows_per_venue is None
            else max_rows_per_venue
        )
        max_compressed_bytes = (
            _SHADOW_RECORDING_MAX_COMPRESSED_BYTES
            if max_compressed_bytes is None
            else max_compressed_bytes
        )
        max_uncompressed_bytes = (
            _SHADOW_RECORDING_MAX_UNCOMPRESSED_BYTES
            if max_uncompressed_bytes is None
            else max_uncompressed_bytes
        )
        if type(max_rows_per_venue) is not int or max_rows_per_venue < 500:
            raise ValueError("wiki_shadow_recording_retention_invalid")
        if type(max_compressed_bytes) is not int or max_compressed_bytes <= 0:
            raise ValueError("wiki_shadow_recording_max_bytes_invalid")
        if type(max_uncompressed_bytes) is not int or max_uncompressed_bytes <= 0:
            raise ValueError("wiki_shadow_recording_max_uncompressed_bytes_invalid")
        self.max_rows_per_venue = max_rows_per_venue
        self.max_compressed_bytes = max_compressed_bytes
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.completion_verifier = completion_verifier
        self._write_uri = str(_write_uri or "")
        self._retention_context = threading.local()
        self._meta_context = threading.local()
        self._rollup_context = threading.local()
        self._comparison_migration_context = threading.local()
        self._after_comparison_insert = _after_comparison_insert

    def _connect(self) -> sqlite3.Connection:
        lock_path = self.db_path.with_name(self.db_path.name + ".lock")
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_fd = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        lock_stat = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_uid != os.getuid()
            or stat.S_IMODE(lock_stat.st_mode) != 0o600
            or lock_stat.st_nlink != 1
        ):
            os.close(lock_fd)
            raise ValueError("wiki_shadow_lock_file_unsafe")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            conn = sqlite3.connect(
                self._write_uri or self.db_path,
                uri=bool(self._write_uri),
                timeout=self.timeout_seconds,
                factory=_LockedConnection,
            )
        except Exception:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            raise
        conn._wiki_lock_fd = lock_fd
        conn.row_factory = sqlite3.Row
        conn.create_function(
            "wiki_shadow_retention_delete_allowed",
            0,
            lambda: int(bool(getattr(self._retention_context, "allowed", False))),
        )
        conn.create_function(
            "wiki_shadow_meta_update_allowed",
            0,
            lambda: int(bool(getattr(self._meta_context, "allowed", False))),
        )
        conn.create_function(
            "wiki_shadow_rollup_update_allowed",
            0,
            lambda: int(bool(getattr(self._rollup_context, "allowed", False))),
        )
        conn.create_function(
            "wiki_shadow_comparison_migration_allowed",
            0,
            lambda: int(
                bool(getattr(self._comparison_migration_context, "allowed", False))
            ),
        )
        def verify_completion(
            provenance_json: str,
            recording_id: str,
            source_prompt_hash: str,
            target_prompt_hash: str,
            response_hash: str,
            recording_created_at: str,
        ) -> int:
            if self.completion_verifier is None:
                return 0
            try:
                provenance = WikiCompletionProvenanceV1.from_dict(
                    json.loads(str(provenance_json or ""))
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                return 0
            return int(
                self.completion_verifier.verify_hashes(
                    provenance,
                    recording_id=str(recording_id or ""),
                    source_prompt_hash=str(source_prompt_hash or ""),
                    target_prompt_hash=str(target_prompt_hash or ""),
                    response_hash=str(response_hash or ""),
                    recording_created_at=str(recording_created_at or ""),
                )
            )

        conn.create_function("wiki_verify_completion_provenance", 6, verify_completion)
        journal_mode = str(
            conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
        ).lower()
        conn.execute("PRAGMA synchronous = FULL")
        synchronous = int(conn.execute("PRAGMA synchronous").fetchone()[0])
        if journal_mode != "delete" or synchronous != 2:
            conn.close()
            raise RuntimeError("wiki_shadow_durable_sqlite_mode_unavailable")
        conn.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        return conn

    def _connect_read_only(self) -> sqlite3.Connection:
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=self.timeout_seconds)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        return conn

    def _recompute_eligibility(
        self,
        conn: sqlite3.Connection,
        venue: str,
    ) -> None:
        if self.completion_verifier is None:
            self._invalidate_eligibility(conn, venue)
            return
        rows = conn.execute(
            """
            SELECT comparison.*, recording.created_at AS recording_created_at
            FROM wiki_shadow_comparisons_v1 AS comparison
            LEFT JOIN wiki_shadow_recordings_v1 AS recording
                ON recording.recording_id = comparison.recording_id
               AND recording.venue = comparison.venue
               AND recording.run_id = comparison.run_id
               AND recording.source_runtime_prompt_hash =
                   comparison.source_prompt_hash
            WHERE comparison.venue = ?
              AND comparison.comparison_status = 'complete'
            """,
            (venue,),
        ).fetchall()
        valid_rows: list[sqlite3.Row] = []
        for row in rows:
            if row["recording_created_at"] is None:
                continue
            try:
                provenance = WikiCompletionProvenanceV1.from_dict(
                    json.loads(str(row["completion_provenance_json"] or ""))
                )
                valid = self.completion_verifier.verify_hashes(
                    provenance,
                    recording_id=str(row["recording_id"]),
                    source_prompt_hash=str(row["source_prompt_hash"]),
                    target_prompt_hash=str(row["target_prompt_hash"]),
                    response_hash=str(row["response_hash"]),
                    recording_created_at=str(row["recording_created_at"]),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                valid = False
            if valid:
                valid_rows.append(row)
                continue
            attempt_id = "wiki-shadow-attempt:quarantine:" + canonical_payload_hash(
                {
                    "comparison_id": row["comparison_id"],
                    "payload_hash": row["payload_hash"],
                }
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO wiki_shadow_replay_attempts_v1 (
                    attempt_id, recording_id, run_id, venue, request_id,
                    payload_json, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    attempt_id,
                    row["recording_id"],
                    row["run_id"],
                    row["venue"],
                    row["payload_json"],
                    row["payload_hash"],
                    row["created_at"],
                ),
            )
            self._comparison_migration_context.allowed = True
            try:
                conn.execute(
                    "DELETE FROM wiki_shadow_comparisons_v1 WHERE comparison_id = ?",
                    (row["comparison_id"],),
                )
            finally:
                self._comparison_migration_context.allowed = False
        now = datetime.now(timezone.utc).isoformat()
        values: dict[str, Any] = {
            "venue": venue,
            "total_complete_count": len(valid_rows),
            "complete_sample_count": sum(bool(row["snapshot_id"]) for row in valid_rows),
            "snapshot_trace_gap_count": sum(
                not row["snapshot_id"] or not bool(row["snapshot_trace_complete"])
                for row in valid_rows
            ),
            "safety_gate_loss_count": sum(
                str(row["safety_gate_loss_json"]) != "[]" for row in valid_rows
            ),
            "required_raw_rag_path_count": sum(
                row["wiki_read_mode"] == "required"
                and str(row["direct_raw_rag_paths_json"]) != "[]"
                for row in valid_rows
            ),
            "outage_new_risk_expansion_count": sum(
                bool(row["simulated_wiki_outage"])
                and bool(row["wiki_induced_new_risk_expansion"])
                for row in valid_rows
            ),
            "evaluated_through": max(
                (str(row["created_at"]) for row in valid_rows),
                default="",
            ),
            "updated_at": now,
        }
        signature = self.completion_verifier.sign_payload(
            "wiki_shadow_eligibility_v1",
            values,
        )
        self._rollup_context.allowed = True
        try:
            conn.execute(
                """
                INSERT INTO wiki_shadow_eligibility_v1 (
                    venue, total_complete_count, complete_sample_count,
                    snapshot_trace_gap_count, safety_gate_loss_count,
                    required_raw_rag_path_count,
                    outage_new_risk_expansion_count,
                    evaluated_through, updated_at, key_id, signature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(venue) DO UPDATE SET
                    total_complete_count = excluded.total_complete_count,
                    complete_sample_count = excluded.complete_sample_count,
                    snapshot_trace_gap_count = excluded.snapshot_trace_gap_count,
                    safety_gate_loss_count = excluded.safety_gate_loss_count,
                    required_raw_rag_path_count = excluded.required_raw_rag_path_count,
                    outage_new_risk_expansion_count = excluded.outage_new_risk_expansion_count,
                    evaluated_through = excluded.evaluated_through,
                    updated_at = excluded.updated_at,
                    key_id = excluded.key_id,
                    signature = excluded.signature
                """,
                (
                    values["venue"], values["total_complete_count"],
                    values["complete_sample_count"],
                    values["snapshot_trace_gap_count"],
                    values["safety_gate_loss_count"],
                    values["required_raw_rag_path_count"],
                    values["outage_new_risk_expansion_count"],
                    values["evaluated_through"], values["updated_at"],
                    self.completion_verifier.key_id, signature,
                ),
            )
        finally:
            self._rollup_context.allowed = False

    def _invalidate_eligibility(
        self,
        conn: sqlite3.Connection,
        venue: str,
    ) -> None:
        self._rollup_context.allowed = True
        try:
            conn.execute(
                "UPDATE wiki_shadow_eligibility_v1 SET key_id = '', signature = '' "
                "WHERE venue = ?",
                (venue,),
            )
        finally:
            self._rollup_context.allowed = False

    def initialize(self) -> None:
        if not self._write_uri:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_shadow_store_meta_v1 (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    max_rows_per_venue INTEGER NOT NULL,
                    max_compressed_bytes INTEGER NOT NULL,
                    max_uncompressed_bytes INTEGER NOT NULL,
                    key_id TEXT NOT NULL DEFAULT ''
                )
                """
            )
            meta_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(wiki_shadow_store_meta_v1)")
            }
            if "key_id" not in meta_columns:
                conn.execute(
                    "ALTER TABLE wiki_shadow_store_meta_v1 "
                    "ADD COLUMN key_id TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_store_meta_v1_no_update
                BEFORE UPDATE ON wiki_shadow_store_meta_v1
                WHEN wiki_shadow_meta_update_allowed() <> 1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_store_meta_immutable');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_store_meta_v1_no_delete
                BEFORE DELETE ON wiki_shadow_store_meta_v1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_store_meta_immutable');
                END
                """
            )
            metadata = conn.execute(
                "SELECT * FROM wiki_shadow_store_meta_v1 WHERE singleton_id = 1"
            ).fetchone()
            if metadata is None:
                conn.execute(
                    "INSERT OR IGNORE INTO wiki_shadow_store_meta_v1 "
                    "VALUES (1, ?, ?, ?, ?)",
                    (
                        self.max_rows_per_venue,
                        self.max_compressed_bytes,
                        self.max_uncompressed_bytes,
                        (
                            self.completion_verifier.key_id
                            if self.completion_verifier is not None
                            else ""
                        ),
                    ),
                )
                metadata = conn.execute(
                    "SELECT * FROM wiki_shadow_store_meta_v1 WHERE singleton_id = 1"
                ).fetchone()
            if metadata is not None:
                stored_key_id = str(metadata["key_id"] or "")
                if self.completion_verifier is not None:
                    if stored_key_id and stored_key_id != self.completion_verifier.key_id:
                        raise ValueError("wiki_shadow_store_key_mismatch")
                    if not stored_key_id:
                        self._meta_context.allowed = True
                        try:
                            conn.execute(
                                "UPDATE wiki_shadow_store_meta_v1 SET key_id = ? "
                                "WHERE singleton_id = 1 AND key_id = ''",
                                (self.completion_verifier.key_id,),
                            )
                        finally:
                            self._meta_context.allowed = False
                stored_limits = (
                    int(metadata["max_rows_per_venue"]),
                    int(metadata["max_compressed_bytes"]),
                    int(metadata["max_uncompressed_bytes"]),
                )
                requested_limits = (
                    self.max_rows_per_venue,
                    self.max_compressed_bytes,
                    self.max_uncompressed_bytes,
                )
                explicit = (
                    self._explicit_max_rows,
                    self._explicit_max_compressed,
                    self._explicit_max_uncompressed,
                )
                if any(
                    is_explicit and requested != stored
                    for is_explicit, requested, stored in zip(
                        explicit,
                        requested_limits,
                        stored_limits,
                    )
                ):
                    raise ValueError("wiki_shadow_store_limits_incompatible")
                (
                    self.max_rows_per_venue,
                    self.max_compressed_bytes,
                    self.max_uncompressed_bytes,
                ) = stored_limits
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_shadow_comparisons_v1 (
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
                    recording_id TEXT NOT NULL DEFAULT '',
                    completion_provenance_json TEXT NOT NULL DEFAULT '',
                    completion_signature TEXT NOT NULL DEFAULT '',
                    completion_request_id TEXT NOT NULL DEFAULT '',
                    source_prompt_hash TEXT NOT NULL DEFAULT '',
                    target_prompt_hash TEXT NOT NULL DEFAULT '',
                    response_hash TEXT NOT NULL DEFAULT '',
                    safety_gate_loss_json TEXT NOT NULL,
                    direct_raw_rag_paths_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    version TEXT NOT NULL DEFAULT 'wiki_shadow_comparison_v1'
                        CHECK (version = 'wiki_shadow_comparison_v1'),
                    created_at TEXT NOT NULL,
                    CHECK (venue IN ('kis', 'binance')),
                    CHECK (comparison_status IN ('complete', 'incomplete', 'error')),
                    CHECK (
                        comparison_status <> 'complete'
                        OR length(trim(snapshot_id)) > 0
                    ),
                    CHECK (wiki_read_mode IN ('shadow', 'prefer', 'required')),
                    CHECK (snapshot_trace_complete IN (0, 1)),
                    CHECK (wiki_induced_new_risk_expansion IN (0, 1)),
                    CHECK (simulated_wiki_outage IN (0, 1)),
                    CHECK (completion_provenance_verified IN (0, 1)),
                    CHECK (
                        comparison_status <> 'complete'
                        OR completion_provenance_verified = 1
                    )
                )
                """
            )
            comparison_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(wiki_shadow_comparisons_v1)")
            }
            if "version" not in comparison_columns:
                conn.execute(
                    """
                    ALTER TABLE wiki_shadow_comparisons_v1
                    ADD COLUMN version TEXT NOT NULL
                    DEFAULT 'wiki_shadow_comparison_v1'
                    CHECK (version = 'wiki_shadow_comparison_v1')
                    """
                )
            if "completion_provenance_verified" not in comparison_columns:
                conn.execute(
                    """
                    ALTER TABLE wiki_shadow_comparisons_v1
                    ADD COLUMN completion_provenance_verified INTEGER NOT NULL
                    DEFAULT 0 CHECK (completion_provenance_verified IN (0, 1))
                    """
                )
            for column_name in (
                "recording_id",
                "completion_provenance_json",
                "completion_signature",
                "completion_request_id",
                "source_prompt_hash",
                "target_prompt_hash",
                "response_hash",
            ):
                if column_name not in comparison_columns:
                    conn.execute(
                        f"ALTER TABLE wiki_shadow_comparisons_v1 "
                        f"ADD COLUMN {column_name} TEXT NOT NULL DEFAULT ''"
                    )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_wiki_shadow_venue_run
                ON wiki_shadow_comparisons_v1 (venue, run_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_shadow_eligibility_v1 (
                    venue TEXT PRIMARY KEY,
                    total_complete_count INTEGER NOT NULL,
                    complete_sample_count INTEGER NOT NULL,
                    snapshot_trace_gap_count INTEGER NOT NULL,
                    safety_gate_loss_count INTEGER NOT NULL,
                    required_raw_rag_path_count INTEGER NOT NULL,
                    outage_new_risk_expansion_count INTEGER NOT NULL,
                    evaluated_through TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    key_id TEXT NOT NULL DEFAULT '',
                    signature TEXT NOT NULL DEFAULT '',
                    CHECK (venue IN ('kis', 'binance'))
                )
                """
            )
            eligibility_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(wiki_shadow_eligibility_v1)")
            }
            for column_name in ("key_id", "signature"):
                if column_name not in eligibility_columns:
                    conn.execute(
                        f"ALTER TABLE wiki_shadow_eligibility_v1 ADD COLUMN "
                        f"{column_name} TEXT NOT NULL DEFAULT ''"
                    )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_eligibility_v1_no_update
                BEFORE UPDATE ON wiki_shadow_eligibility_v1
                WHEN wiki_shadow_rollup_update_allowed() <> 1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_eligibility_immutable');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_eligibility_v1_no_delete
                BEFORE DELETE ON wiki_shadow_eligibility_v1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_eligibility_immutable');
                END
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_shadow_replay_attempts_v1 (
                    attempt_id TEXT PRIMARY KEY,
                    recording_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    venue TEXT NOT NULL CHECK (venue IN ('kis', 'binance')),
                    request_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("DROP INDEX IF EXISTS ux_wiki_shadow_attempt_request_id")
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_replay_attempts_v1_no_update
                BEFORE UPDATE ON wiki_shadow_replay_attempts_v1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_replay_attempt_immutable');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_replay_attempts_v1_no_delete
                BEFORE DELETE ON wiki_shadow_replay_attempts_v1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_replay_attempt_immutable');
                END
                """
            )
            conn.execute(
                "DROP TRIGGER IF EXISTS wiki_shadow_comparisons_v1_no_update"
            )
            conn.execute(
                "DROP TRIGGER IF EXISTS wiki_shadow_comparisons_v1_no_delete"
            )
            legacy_unverified_rows = conn.execute(
                """
                SELECT comparison_id, run_id, venue, payload_json,
                       payload_hash, created_at
                FROM wiki_shadow_comparisons_v1
                WHERE completion_provenance_verified <> 1
                   OR recording_id = ''
                   OR completion_provenance_json = ''
                   OR completion_signature = ''
                """
            ).fetchall()
            for legacy_row in legacy_unverified_rows:
                migrated_request_id = ""
                try:
                    migrated_payload = json.loads(legacy_row["payload_json"])
                    migrated_provenance = WikiCompletionProvenanceV1.from_dict(
                        json.loads(
                            str(
                                migrated_payload.get(
                                    "completion_provenance_json",
                                    "",
                                )
                            )
                        )
                    )
                    migrated_request_id = migrated_provenance.request_id
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
                attempt_id = "wiki-shadow-attempt:migrated:" + canonical_payload_hash(
                    {
                        "comparison_id": legacy_row["comparison_id"],
                        "payload_hash": legacy_row["payload_hash"],
                    }
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO wiki_shadow_replay_attempts_v1 (
                        attempt_id, recording_id, run_id, venue, request_id,
                        payload_json, payload_hash, created_at
                    ) VALUES (?, '', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        legacy_row["run_id"],
                        legacy_row["venue"],
                        migrated_request_id,
                        legacy_row["payload_json"],
                        legacy_row["payload_hash"],
                        legacy_row["created_at"],
                    ),
                )
                conn.execute(
                    "DELETE FROM wiki_shadow_comparisons_v1 WHERE comparison_id = ?",
                    (legacy_row["comparison_id"],),
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_runtime_prompt_envelopes_v1 (
                    envelope_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL CHECK (venue IN ('kis', 'binance')),
                    runtime_prompt_hash TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_shadow_recordings_v1 (
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
                    UNIQUE (venue, run_id)
                )
                """
            )
            recording_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(wiki_shadow_recordings_v1)")
            }
            if "uncompressed_bytes" not in recording_columns:
                conn.execute(
                    """
                    ALTER TABLE wiki_shadow_recordings_v1
                    ADD COLUMN uncompressed_bytes INTEGER NOT NULL DEFAULT 1
                    """
                )
            legacy_manager_unique = False
            for index_row in conn.execute(
                "PRAGMA index_list(wiki_shadow_recordings_v1)"
            ).fetchall():
                if int(index_row["unique"]) != 1:
                    continue
                index_name = str(index_row["name"])
                index_columns = tuple(
                    str(column_row["name"])
                    for column_row in conn.execute(
                        f'PRAGMA index_info("{index_name}")'
                    ).fetchall()
                )
                if index_columns == ("venue", "manager_run_id"):
                    legacy_manager_unique = True
                    break
            if legacy_manager_unique:
                for trigger_name in (
                    "wiki_shadow_recordings_v1_no_update",
                    "wiki_shadow_recordings_v1_no_replace",
                    "wiki_shadow_recordings_v1_controlled_delete",
                ):
                    conn.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
                conn.execute(
                    "ALTER TABLE wiki_shadow_recordings_v1 "
                    "RENAME TO wiki_shadow_recordings_v1_legacy_manager_unique"
                )
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
                        uncompressed_bytes INTEGER NOT NULL
                            CHECK (uncompressed_bytes > 0),
                        compressed_bytes INTEGER NOT NULL
                            CHECK (compressed_bytes > 0),
                        version TEXT NOT NULL
                            CHECK (version = 'wiki_shadow_recording_v1'),
                        created_at TEXT NOT NULL,
                        UNIQUE (venue, run_id)
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO wiki_shadow_recordings_v1 (
                        recording_id, venue, run_id, manager_run_id, snapshot_id,
                        source_runtime_prompt_hash, payload_zlib, payload_hash,
                        uncompressed_bytes, compressed_bytes, version, created_at
                    )
                    SELECT recording_id, venue, run_id, manager_run_id, snapshot_id,
                           source_runtime_prompt_hash, payload_zlib, payload_hash,
                           uncompressed_bytes, compressed_bytes, version, created_at
                    FROM wiki_shadow_recordings_v1_legacy_manager_unique
                    """
                )
                conn.execute(
                    "DROP TABLE wiki_shadow_recordings_v1_legacy_manager_unique"
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_wiki_shadow_recording_manager_run
                ON wiki_shadow_recordings_v1 (venue, manager_run_id, created_at)
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_comparisons_v1_no_duplicate
                BEFORE INSERT ON wiki_shadow_comparisons_v1
                WHEN EXISTS (
                    SELECT 1 FROM wiki_shadow_comparisons_v1
                    WHERE comparison_id = NEW.comparison_id
                       OR (venue = NEW.venue AND run_id = NEW.run_id)
                )
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_duplicate_identity');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_runtime_prompt_envelopes_v1_no_update
                BEFORE UPDATE ON wiki_runtime_prompt_envelopes_v1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_runtime_prompt_envelope_immutable');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_runtime_prompt_envelopes_v1_no_duplicate
                BEFORE INSERT ON wiki_runtime_prompt_envelopes_v1
                WHEN EXISTS (
                    SELECT 1 FROM wiki_runtime_prompt_envelopes_v1
                    WHERE envelope_id = NEW.envelope_id
                       OR runtime_prompt_hash = NEW.runtime_prompt_hash
                )
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_runtime_prompt_envelope_duplicate');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_runtime_prompt_envelopes_v1_no_delete
                BEFORE DELETE ON wiki_runtime_prompt_envelopes_v1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_runtime_prompt_envelope_immutable');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_recordings_v1_no_update
                BEFORE UPDATE ON wiki_shadow_recordings_v1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_recording_immutable');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_recordings_v1_no_replace
                BEFORE INSERT ON wiki_shadow_recordings_v1
                WHEN EXISTS (
                    SELECT 1 FROM wiki_shadow_recordings_v1
                    WHERE recording_id = NEW.recording_id
                       OR (venue = NEW.venue AND run_id = NEW.run_id)
                )
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_recording_duplicate_identity');
                END
                """
            )
            delete_trigger = conn.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'trigger'
                  AND name = 'wiki_shadow_recordings_v1_controlled_delete'
                """
            ).fetchone()
            if delete_trigger is not None and "retention_control" in str(
                delete_trigger["sql"] or ""
            ):
                conn.execute(
                    "DROP TRIGGER wiki_shadow_recordings_v1_controlled_delete"
                )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_recordings_v1_controlled_delete
                BEFORE DELETE ON wiki_shadow_recordings_v1
                WHEN wiki_shadow_retention_delete_allowed() <> 1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_recording_delete_not_authorized');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_comparisons_v1_normalized_identity
                BEFORE INSERT ON wiki_shadow_comparisons_v1
                WHEN NEW.run_id <> trim(NEW.run_id)
                  OR NEW.venue <> lower(trim(NEW.venue))
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_identity_not_normalized');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_comparisons_v1_complete_snapshot
                BEFORE INSERT ON wiki_shadow_comparisons_v1
                WHEN NEW.comparison_status = 'complete'
                 AND length(trim(NEW.snapshot_id)) = 0
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_complete_snapshot_required');
                END
                """
            )
            conn.execute(
                "DROP TRIGGER IF EXISTS "
                "wiki_shadow_comparisons_v1_complete_provenance"
            )
            conn.execute(
                """
                CREATE TRIGGER
                wiki_shadow_comparisons_v1_complete_provenance
                BEFORE INSERT ON wiki_shadow_comparisons_v1
                WHEN NEW.comparison_status = 'complete'
                 AND (
                    NEW.completion_provenance_verified <> 1
                    OR NOT EXISTS (
                        SELECT 1 FROM wiki_shadow_recordings_v1 AS recording
                        WHERE recording.recording_id = NEW.recording_id
                          AND recording.run_id = NEW.run_id
                          AND recording.venue = NEW.venue
                          AND recording.source_runtime_prompt_hash =
                              NEW.source_prompt_hash
                    )
                    OR wiki_verify_completion_provenance(
                        NEW.completion_provenance_json,
                        NEW.recording_id,
                        NEW.source_prompt_hash,
                        NEW.target_prompt_hash,
                        NEW.response_hash,
                        (
                            SELECT recording.created_at
                            FROM wiki_shadow_recordings_v1 AS recording
                            WHERE recording.recording_id = NEW.recording_id
                        )
                    ) <> 1
                    OR EXISTS (
                        SELECT 1 FROM wiki_shadow_comparisons_v1
                        WHERE completion_request_id = NEW.completion_request_id
                    )
                 )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'wiki_shadow_complete_completion_provenance_required'
                    );
                END
                """
            )
            for venue in sorted(_VENUES):
                self._recompute_eligibility(conn, venue)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_wiki_shadow_venue_status_created
                ON wiki_shadow_comparisons_v1 (
                    venue, comparison_status, created_at, comparison_id
                )
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_comparisons_v1_no_update
                BEFORE UPDATE ON wiki_shadow_comparisons_v1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_comparison_immutable');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS wiki_shadow_comparisons_v1_no_delete
                BEFORE DELETE ON wiki_shadow_comparisons_v1
                WHEN wiki_shadow_comparison_migration_allowed() <> 1
                BEGIN
                    SELECT RAISE(ABORT, 'wiki_shadow_comparison_immutable');
                END
                """
            )
        if not self._write_uri and self.db_path.exists():
            os.chmod(self.db_path, 0o600)

    def record(self, comparison: WikiShadowComparisonV1) -> str:
        if _utc_instant(comparison.created_at) > datetime.now(timezone.utc):
            raise ValueError("wiki_shadow_created_at_future")
        payload_json = _canonical_json(comparison.to_dict())
        payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
        if comparison.comparison_status != "complete":
            attempt_id = f"wiki-shadow-attempt:{payload_hash}"
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT attempt_id FROM wiki_shadow_replay_attempts_v1 "
                    "WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()
                if existing is not None:
                    return attempt_id
                conn.execute(
                    """
                    INSERT OR IGNORE INTO wiki_shadow_replay_attempts_v1 (
                        attempt_id, recording_id, run_id, venue, request_id,
                        payload_json, payload_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        comparison.recording_id,
                        comparison.run_id,
                        comparison.venue,
                        "",
                        payload_json,
                        payload_hash,
                        comparison.created_at,
                    ),
                )
            return attempt_id
        comparison_id = comparison.comparison_id
        provenance = WikiCompletionProvenanceV1.from_dict(
            json.loads(comparison.completion_provenance_json)
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT payload_hash
                FROM wiki_shadow_comparisons_v1
                WHERE comparison_id = ?
                """,
                (comparison_id,),
            ).fetchone()
            if existing is not None:
                self._recompute_eligibility(conn, comparison.venue)
                existing = conn.execute(
                    "SELECT payload_hash FROM wiki_shadow_comparisons_v1 "
                    "WHERE comparison_id = ?",
                    (comparison_id,),
                ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash:
                    raise ValueError(
                        f"wiki_shadow_identity_collision:{comparison_id}"
                    )
                return comparison_id
            conn.execute(
                """
                INSERT INTO wiki_shadow_comparisons_v1 (
                    comparison_id, run_id, venue, snapshot_id,
                    comparison_status, wiki_read_mode, snapshot_trace_complete,
                    wiki_induced_new_risk_expansion, simulated_wiki_outage,
                    completion_provenance_verified,
                    recording_id, completion_provenance_json,
                    completion_signature, completion_request_id,
                    source_prompt_hash, target_prompt_hash, response_hash,
                    safety_gate_loss_json, direct_raw_rag_paths_json,
                    payload_json, payload_hash, version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    comparison_id,
                    comparison.run_id,
                    comparison.venue,
                    comparison.snapshot_id,
                    comparison.comparison_status,
                    comparison.wiki_read_mode,
                    int(comparison.snapshot_trace_complete),
                    int(comparison.wiki_induced_new_risk_expansion),
                    int(comparison.simulated_wiki_outage),
                    int(comparison.completion_provenance_verified),
                    comparison.recording_id,
                    comparison.completion_provenance_json,
                    provenance.signature,
                    provenance.request_id,
                    provenance.source_prompt_hash,
                    provenance.target_prompt_hash,
                    provenance.response_hash,
                    _canonical_json(comparison.safety_gate_loss),
                    _canonical_json(comparison.direct_raw_rag_paths),
                    payload_json,
                    payload_hash,
                    comparison.version,
                    comparison.created_at,
                ),
            )
            if self._after_comparison_insert is not None:
                self._after_comparison_insert()
            self._recompute_eligibility(conn, comparison.venue)
        return comparison_id

    def record_runtime_envelope(
        self,
        envelope: WikiRuntimePromptEnvelopeV1,
        *,
        max_chars: int = 250_000,
    ) -> str:
        payload_json = _canonical_json(envelope.to_dict())
        if len(envelope.runtime_prompt_json) > max(int(max_chars), 1):
            raise ValueError("wiki_runtime_envelope_prompt_too_large")
        payload_hash = canonical_payload_hash(envelope.to_dict())
        envelope_id = f"wiki-envelope:{envelope.wiki_runtime_prompt_hash}"
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT payload_hash FROM wiki_runtime_prompt_envelopes_v1
                WHERE envelope_id = ?
                """,
                (envelope_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash:
                    raise ValueError("wiki_runtime_envelope_identity_collision")
                return envelope_id
            conn.execute(
                """
                INSERT INTO wiki_runtime_prompt_envelopes_v1 (
                    envelope_id, venue, runtime_prompt_hash,
                    payload_json, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope_id,
                    envelope.venue,
                    envelope.wiki_runtime_prompt_hash,
                    payload_json,
                    payload_hash,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return envelope_id

    def record_shadow_recording(
        self,
        recording: WikiShadowRecordingV1,
    ) -> str:
        payload_json = _canonical_json(recording.to_dict())
        payload_bytes = payload_json.encode("utf-8")
        if len(payload_bytes) > self.max_uncompressed_bytes:
            raise ValueError("wiki_shadow_recording_uncompressed_payload_too_large")
        payload_zlib = zlib.compress(payload_bytes, level=9)
        if len(payload_zlib) > self.max_compressed_bytes:
            raise ValueError("wiki_shadow_recording_payload_too_large")
        payload_hash = canonical_payload_hash(recording.to_dict())
        with self._connect() as conn:
            conn.execute("PRAGMA secure_delete = ON")
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT recording_id, payload_hash
                FROM wiki_shadow_recordings_v1
                WHERE recording_id = ?
                   OR (venue = ? AND run_id = ?)
                """,
                (
                    recording.recording_id,
                    recording.venue,
                    recording.run_id,
                ),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash:
                    raise ValueError("wiki_shadow_recording_identity_collision")
                return str(existing["recording_id"])
            conn.execute(
                """
                INSERT INTO wiki_shadow_recordings_v1 (
                    recording_id, venue, run_id, manager_run_id, snapshot_id,
                    source_runtime_prompt_hash, payload_zlib, payload_hash,
                    uncompressed_bytes, compressed_bytes, version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recording.recording_id,
                    recording.venue,
                    recording.run_id,
                    recording.manager_run_id,
                    recording.snapshot_id,
                    recording.source_runtime_prompt_hash,
                    payload_zlib,
                    payload_hash,
                    len(payload_bytes),
                    len(payload_zlib),
                    recording.version,
                    recording.created_at,
                ),
            )
            self._retention_context.allowed = True
            try:
                deleted = conn.execute(
                    """
                    DELETE FROM wiki_shadow_recordings_v1
                    WHERE recording_id IN (
                        SELECT recording_id FROM wiki_shadow_recordings_v1
                        WHERE venue = ?
                        ORDER BY created_at DESC, recording_id DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (recording.venue, self.max_rows_per_venue),
                )
                if deleted.rowcount:
                    self._recompute_eligibility(conn, recording.venue)
            finally:
                self._retention_context.allowed = False
        return recording.recording_id

    def recording(
        self,
        venue: str,
        *,
        run_id: str = "",
        manager_run_id: str | int = "",
    ) -> WikiShadowRecordingV1 | None:
        self._load_authoritative_limits()
        clean_venue = str(venue or "").strip().lower()
        clean_run_id = str(run_id or "").strip()
        clean_manager_run_id = str(manager_run_id or "").strip()
        if clean_venue not in _VENUES or not (clean_run_id or clean_manager_run_id):
            raise ValueError("wiki_shadow_recording_lookup_invalid")
        clauses = ["venue = ?"]
        params: list[Any] = [clean_venue]
        if clean_run_id:
            clauses.append("run_id = ?")
            params.append(clean_run_id)
        if clean_manager_run_id:
            clauses.append("manager_run_id = ?")
            params.append(clean_manager_run_id)
        with self._connect_read_only() as conn:
            rows = conn.execute(
                "SELECT payload_zlib, uncompressed_bytes, compressed_bytes "
                "FROM wiki_shadow_recordings_v1 WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, recording_id DESC LIMIT 2",
                params,
            ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError("wiki_shadow_recording_lookup_ambiguous")
        row = rows[0]
        compressed = bytes(row["payload_zlib"])
        if (
            len(compressed) != int(row["compressed_bytes"])
            or len(compressed) > self.max_compressed_bytes
            or int(row["uncompressed_bytes"]) > self.max_uncompressed_bytes
        ):
            raise ValueError("wiki_shadow_recording_payload_bounds_invalid")
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(
                compressed,
                self.max_uncompressed_bytes + 1,
            )
            if (
                len(raw) > self.max_uncompressed_bytes
                or len(raw) != int(row["uncompressed_bytes"])
                or not decoder.eof
                or decoder.unused_data
                or decoder.unconsumed_tail
            ):
                raise ValueError("wiki_shadow_recording_payload_bounds_invalid")
            payload = json.loads(raw)
        except (TypeError, ValueError, zlib.error, json.JSONDecodeError) as exc:
            raise ValueError("wiki_shadow_recording_payload_corrupt") from exc
        return WikiShadowRecordingV1(**payload)

    def recording_status(self) -> dict[str, Any]:
        self._load_authoritative_limits()
        with self._connect_read_only() as conn:
            rows = conn.execute(
                """
                SELECT venue, COUNT(*) AS row_count,
                       COALESCE(SUM(uncompressed_bytes), 0) AS uncompressed_bytes,
                       COALESCE(SUM(compressed_bytes), 0) AS compressed_bytes
                FROM wiki_shadow_recordings_v1 GROUP BY venue
                """
            ).fetchall()
        by_venue = {
            str(row["venue"]): {
                "row_count": int(row["row_count"]),
                "uncompressed_bytes": int(row["uncompressed_bytes"]),
                "compressed_bytes": int(row["compressed_bytes"]),
            }
            for row in rows
        }
        return {
            "version": "wiki_shadow_recording_status_v1",
            "max_rows_per_venue": self.max_rows_per_venue,
            "max_compressed_bytes_per_record": self.max_compressed_bytes,
            "max_uncompressed_bytes_per_record": self.max_uncompressed_bytes,
            "max_compressed_bytes_per_venue": (
                self.max_rows_per_venue * self.max_compressed_bytes
            ),
            "venues": by_venue,
            "total_rows": sum(row["row_count"] for row in by_venue.values()),
            "total_compressed_bytes": sum(
                row["compressed_bytes"] for row in by_venue.values()
            ),
            "total_uncompressed_bytes": sum(
                row["uncompressed_bytes"] for row in by_venue.values()
            ),
        }

    def _load_authoritative_limits(self) -> None:
        if not self.db_path.exists():
            return
        with self._connect_read_only() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM wiki_shadow_store_meta_v1 "
                    "WHERE singleton_id = 1"
                ).fetchone()
            except sqlite3.OperationalError:
                return
        if row is None:
            return
        stored_limits = (
            int(row["max_rows_per_venue"]),
            int(row["max_compressed_bytes"]),
            int(row["max_uncompressed_bytes"]),
        )
        requested_limits = (
            self.max_rows_per_venue,
            self.max_compressed_bytes,
            self.max_uncompressed_bytes,
        )
        explicit = (
            self._explicit_max_rows,
            self._explicit_max_compressed,
            self._explicit_max_uncompressed,
        )
        if any(
            is_explicit and requested != stored
            for is_explicit, requested, stored in zip(
                explicit,
                requested_limits,
                stored_limits,
            )
        ):
            raise ValueError("wiki_shadow_store_limits_incompatible")
        (
            self.max_rows_per_venue,
            self.max_compressed_bytes,
            self.max_uncompressed_bytes,
        ) = stored_limits

    def eligibility(self, venue: str) -> dict[str, Any]:
        clean_venue = str(venue or "").strip().lower()
        if clean_venue not in _VENUES:
            raise ValueError("wiki_shadow_venue_invalid")
        with self._connect_read_only() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wiki_shadow_eligibility_v1
                WHERE venue = ?
                """,
                (clean_venue,),
            ).fetchone()
        values = dict(row) if row is not None else {}
        sample_count = int(values.get("complete_sample_count") or 0)
        blockers: list[str] = []
        signature_payload = {
            field: values.get(field, "" if field in {"venue", "evaluated_through", "updated_at"} else 0)
            for field in (
                "venue", "total_complete_count", "complete_sample_count",
                "snapshot_trace_gap_count", "safety_gate_loss_count",
                "required_raw_rag_path_count", "outage_new_risk_expansion_count",
                "evaluated_through", "updated_at",
            )
        }
        if (
            self.completion_verifier is None
            or str(values.get("key_id") or "") != self.completion_verifier.key_id
            or not self.completion_verifier.verify_payload(
                "wiki_shadow_eligibility_v1",
                signature_payload,
                str(values.get("signature") or ""),
            )
        ):
            blockers.append("eligibility_signature_invalid")
        if sample_count < _MIN_REQUIRED_SAMPLES:
            blockers.append("insufficient_complete_comparisons")
        if int(values.get("safety_gate_loss_count") or 0):
            blockers.append("safety_gate_divergence")
        if int(values.get("required_raw_rag_path_count") or 0):
            blockers.append("required_mode_raw_rag_path_present")
        if int(values.get("snapshot_trace_gap_count") or 0):
            blockers.append("snapshot_trace_incomplete")
        if int(values.get("outage_new_risk_expansion_count") or 0):
            blockers.append("wiki_outage_new_risk_expansion")
        freshness_reason = wiki_eligibility_freshness_reason(
            {
                "evaluated_at": str(values.get("updated_at") or ""),
                "evaluated_through": str(values.get("evaluated_through") or ""),
            },
            now=datetime.now(timezone.utc),
        )
        if freshness_reason:
            blockers.append(freshness_reason)
        return {
            "version": "wiki_shadow_eligibility_v1",
            "venue": clean_venue,
            "required_eligible": not blockers,
            "reason": blockers[0] if blockers else "required_acceptance_gates_passed",
            "blockers": blockers,
            "complete_sample_count": sample_count,
            "minimum_complete_sample_count": _MIN_REQUIRED_SAMPLES,
            "total_complete_count": int(values.get("total_complete_count") or 0),
            "snapshot_trace_gap_count": int(
                values.get("snapshot_trace_gap_count") or 0
            ),
            "safety_gate_loss_count": int(values.get("safety_gate_loss_count") or 0),
            "required_raw_rag_path_count": int(
                values.get("required_raw_rag_path_count") or 0
            ),
            "outage_new_risk_expansion_count": int(
                values.get("outage_new_risk_expansion_count") or 0
            ),
            "evaluated_at": str(values.get("updated_at") or ""),
            "evaluated_through": str(values.get("evaluated_through") or ""),
        }


@dataclass(frozen=True)
class WikiRuntimeEnvelopeRecorder:
    db_path: Path
    max_chars: int = 250_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", Path(self.db_path))
        if type(self.max_chars) is not int or self.max_chars <= 0:
            raise ValueError("wiki_runtime_envelope_max_chars_invalid")

    def __call__(self, envelope: WikiRuntimePromptEnvelopeV1) -> str:
        return JueWikiShadowStore(self.db_path).record_runtime_envelope(
            envelope,
            max_chars=self.max_chars,
        )


def build_runtime_envelope_recorder(
    db_path: str | Path,
    *,
    max_chars: int = 250_000,
) -> WikiRuntimeEnvelopeRecorder:
    return WikiRuntimeEnvelopeRecorder(Path(db_path), max_chars=max_chars)


@dataclass(frozen=True)
class WikiShadowRecordingRecorder:
    db_path: Path
    completion_verifier: WikiCompletionSigner
    max_rows_per_venue: int = _SHADOW_RECORDING_MAX_ROWS_PER_VENUE
    max_compressed_bytes: int = _SHADOW_RECORDING_MAX_COMPRESSED_BYTES
    max_uncompressed_bytes: int = _SHADOW_RECORDING_MAX_UNCOMPRESSED_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", Path(self.db_path))
        if type(self.max_rows_per_venue) is not int or self.max_rows_per_venue < 500:
            raise ValueError("wiki_shadow_recording_retention_invalid")
        if type(self.max_compressed_bytes) is not int or self.max_compressed_bytes <= 0:
            raise ValueError("wiki_shadow_recording_max_bytes_invalid")
        if type(self.max_uncompressed_bytes) is not int or self.max_uncompressed_bytes <= 0:
            raise ValueError("wiki_shadow_recording_max_uncompressed_bytes_invalid")

    def __call__(self, recording: WikiShadowRecordingV1) -> str:
        store = self._store()
        store.initialize()
        return store.record_shadow_recording(recording)

    def _store(self) -> JueWikiShadowStore:
        return JueWikiShadowStore(
            self.db_path,
            completion_verifier=self.completion_verifier,
            max_rows_per_venue=(
                None
                if self.max_rows_per_venue == _SHADOW_RECORDING_MAX_ROWS_PER_VENUE
                else self.max_rows_per_venue
            ),
            max_compressed_bytes=(
                None
                if self.max_compressed_bytes == _SHADOW_RECORDING_MAX_COMPRESSED_BYTES
                else self.max_compressed_bytes
            ),
            max_uncompressed_bytes=(
                None
                if self.max_uncompressed_bytes
                == _SHADOW_RECORDING_MAX_UNCOMPRESSED_BYTES
                else self.max_uncompressed_bytes
            ),
        )

    def recording(
        self,
        venue: str,
        *,
        run_id: str = "",
        manager_run_id: str | int = "",
    ) -> WikiShadowRecordingV1 | None:
        return self._store().recording(
            venue,
            run_id=run_id,
            manager_run_id=manager_run_id,
        )

    def status(self) -> dict[str, Any]:
        return self._store().recording_status()


def build_runtime_recording_recorder(
    db_path: str | Path,
    *,
    provenance_key_path: str | Path | None = None,
    completion_verifier: WikiCompletionSigner | None = None,
    max_rows_per_venue: int = _SHADOW_RECORDING_MAX_ROWS_PER_VENUE,
    max_compressed_bytes: int = _SHADOW_RECORDING_MAX_COMPRESSED_BYTES,
    max_uncompressed_bytes: int = _SHADOW_RECORDING_MAX_UNCOMPRESSED_BYTES,
) -> WikiShadowRecordingRecorder:
    if completion_verifier is None:
        if provenance_key_path is None:
            raise ValueError("wiki_shadow_recording_signer_required")
        completion_verifier = WikiCompletionSigner(provenance_key_path)
    elif provenance_key_path is not None:
        raise ValueError("wiki_shadow_recording_signer_ambiguous")
    return WikiShadowRecordingRecorder(
        Path(db_path),
        completion_verifier=completion_verifier,
        max_rows_per_venue=max_rows_per_venue,
        max_compressed_bytes=max_compressed_bytes,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple)) else []
    return tuple(sorted({str(item).strip() for item in values if str(item).strip()}))


def _validate_action_shapes(response: dict[str, Any]) -> None:
    for key in _ACTION_KEYS:
        value = response.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"{key}_must_be_list")
        if any(not isinstance(row, dict) for row in value):
            raise ValueError(f"{key}_items_must_be_objects")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value] if isinstance(value, list) else []


def _quotes_by_symbol(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        return {
            str(symbol): dict(row)
            for symbol, row in value.items()
            if isinstance(row, dict)
        }
    return {
        str(row.get("symbol") or ""): dict(row)
        for row in _rows(value)
        if str(row.get("symbol") or "")
    }


_SAFETY_SECTION_KEYS = (
    "execution_gate",
    "kill_switch",
    "live_authority",
    "entry_gate_policy",
    "risk_guard",
    "risk_limits",
    "policy_rule_evaluation",
    "growth_governor",
)


def _safety_section_hashes(prompt: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (key, canonical_payload_hash(prompt[key]))
        for key in _SAFETY_SECTION_KEYS
        if key in prompt
    )


_BLOCKING_STATUS_VALUES = frozenset(
    {
        "blocked",
        "blocked_by_validation",
        "disabled",
        "error",
        "fail",
        "failed",
        "halt",
        "halt_new_risk",
        "risk_off",
        "restricted",
        "unavailable",
    }
)
_DENY_BOOLEAN_KEYS = frozenset(
    {
        "allow_new_entries",
        "allow_new_risk",
        "allow_scale_up",
        "new_entry_allowed",
        "new_risk_allowed",
    }
)
_HALT_BOOLEAN_KEYS = frozenset(
    {"blocked", "halt_new_risk", "kill_switch_enabled", "risk_off"}
)


def _section_safety_blockers(section_name: str, value: Any) -> tuple[str, ...]:
    blockers: set[str] = set()

    def visit(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for raw_key, child in node.items():
                key = str(raw_key).strip().lower()
                child_path = f"{path}.{key}" if path else key
                if key in {"status", "readiness", "risk_governor_action", "action"}:
                    status = str(child or "").strip().lower()
                    if status in _BLOCKING_STATUS_VALUES or status.startswith("blocked"):
                        blockers.add(f"{section_name}:{child_path}:{status}")
                if key in _DENY_BOOLEAN_KEYS and child is False:
                    blockers.add(f"{section_name}:{child_path}:false")
                if key in _HALT_BOOLEAN_KEYS and child is True:
                    blockers.add(f"{section_name}:{child_path}:true")
                if key == "enabled" and path.endswith("kill_switch") and child is True:
                    blockers.add("kill_switch")
                visit(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]")

    visit(value, "")
    return tuple(sorted(blockers))


def _local_safety_state(
    venue: str,
    prompt: dict[str, Any],
) -> tuple[tuple[str, ...], bool, bool, bool]:
    gate = prompt.get("execution_gate")
    if not isinstance(gate, dict):
        return ("execution_gate_missing",), False, False, False
    expected_version = f"{venue}_execution_gate_v1"
    status = gate.get("status")
    kill = gate.get("kill_switch")
    if (
        gate.get("version") != expected_version
        or not isinstance(status, str)
        or not isinstance(kill, dict)
        or type(kill.get("enabled")) is not bool
    ):
        return ("execution_gate_invalid",), False, False, False
    blockers: list[str] = []
    if kill["enabled"]:
        blockers.append("kill_switch")
    if status.strip().lower() not in {"ok", "ready"}:
        blockers.append("execution_gate")
    if venue == "kis":
        execute_orders = gate.get("execute_orders")
        session_allowed = gate.get("new_entry_allowed_by_session")
        if type(execute_orders) is not bool or type(session_allowed) is not bool:
            return ("execution_gate_invalid",), False, False, False
        if not execute_orders:
            blockers.append("orders_disabled")
        if not session_allowed:
            blockers.append("session_gate")
        execution_allowed = execute_orders and not kill["enabled"]
        new_risk_allowed = execution_allowed and session_allowed and not blockers
    else:
        execution = gate.get("execution")
        if not isinstance(execution, dict):
            return ("execution_gate_invalid",), False, False, False
        flags = tuple(
            execution.get(key)
            for key in (
                "spot_orders_enabled",
                "futures_orders_enabled",
                "upbit_orders_enabled",
            )
        )
        if any(type(value) is not bool for value in flags):
            return ("execution_gate_invalid",), False, False, False
        if not any(flags):
            blockers.append("orders_disabled")
        execution_allowed = any(flags) and not kill["enabled"]
        new_risk_allowed = execution_allowed and not blockers
    safety_inputs_valid = True
    for section_name in _SAFETY_SECTION_KEYS:
        if section_name == "execution_gate" or section_name not in prompt:
            continue
        section = prompt.get(section_name)
        if not isinstance(section, dict):
            blockers.append(f"{section_name}_invalid")
            safety_inputs_valid = False
            continue
        blockers.extend(_section_safety_blockers(section_name, section))
    new_risk_allowed = bool(new_risk_allowed and not blockers)
    return (
        tuple(sorted(set(blockers))),
        execution_allowed,
        new_risk_allowed,
        safety_inputs_valid,
    )


def _production_safety_permissions(
    venue: str,
    prompt: dict[str, Any],
) -> tuple[bool, bool]:
    _blockers, execution_allowed, _new_risk, valid = _local_safety_state(
        venue,
        prompt,
    )
    allow_create = bool(valid and execution_allowed)
    allow_scale = bool(valid and execution_allowed)

    def visit(node: Any, path: str = "") -> None:
        nonlocal allow_create, allow_scale
        if isinstance(node, dict):
            for raw_key, child in node.items():
                key = str(raw_key).strip().lower()
                child_path = f"{path}.{key}" if path else key
                text = str(child or "").strip().lower()
                if key in {"allow_new_risk", "allow_new_entries"} and child is False:
                    allow_create = False
                    allow_scale = False
                if key == "allow_scale_up" and child is False:
                    allow_scale = False
                if key in {"allow_new_blocks", "new_entry_allowed"} and child is False:
                    allow_create = False
                if key in {"max_new_blocks", "new_block_cap", "aggression_cap"}:
                    try:
                        if not isinstance(child, bool) and float(child) <= 0:
                            allow_create = False
                    except (TypeError, ValueError):
                        allow_create = False
                if key == "mode" and text == "halt_new_entries":
                    allow_create = False
                if key in {"risk_governor_action", "action"} and text in {
                    "halt_new_risk",
                    "risk_off",
                }:
                    allow_create = False
                    allow_scale = False
                if key in {"status", "readiness"} and (
                    text in {"error", "failed", "blocked_by_validation"}
                    or text.startswith("blocked")
                ) and any(
                    token in child_path
                    for token in ("live_authority", "validation", "risk", "policy")
                ):
                    allow_create = False
                    allow_scale = False
                visit(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]")

    for section_name in _SAFETY_SECTION_KEYS:
        if section_name in prompt:
            visit(prompt[section_name], section_name)
    return allow_create, allow_scale


def _apply_production_safety_permissions(
    actions: dict[str, list[dict[str, Any]]],
    *,
    venue: str,
    blocks: list[dict[str, Any]],
    allow_create: bool,
    allow_scale: bool,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    filtered = {key: [dict(row) for row in actions.get(key, [])] for key in _ACTION_KEYS}
    suppressed = 0
    if not allow_create:
        suppressed += len(filtered["create_blocks"])
        filtered["create_blocks"] = []
    if not allow_scale:
        block_index = {
            str(row.get("block_id") or ""): row
            for row in blocks
            if str(row.get("block_id") or "")
        }
        classifier = (
            kis_update_adds_new_risk
            if venue == "kis"
            else binance_update_adds_new_risk
        )
        kept: list[dict[str, Any]] = []
        for row in filtered["update_blocks"]:
            block_id = str(row.get("block_id") or "")
            if classifier(row, block_index.get(block_id, {})):
                suppressed += 1
            else:
                kept.append(row)
        filtered["update_blocks"] = kept
    return filtered, suppressed


def _validated_actions(
    *,
    venue: str,
    prompt: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    _validate_action_shapes(response)
    if venue == "binance":
        def normalize_create(value: dict[str, Any]) -> dict[str, Any]:
            row = dict(value)
            apply_manager_contract_aliases(row)
            market = normalize_market(row.get("market") or row.get("venue"))
            if market:
                row["market"] = market
            side = normalize_position_side(row.get("side"))
            if side:
                row["side"] = side
            row["horizon"] = normalize_binance_horizon(
                row.get("horizon"),
                market=market,
            )
            return row

        actions = validate_binance_manager_actions(
            response,
            normalize_create_payload=normalize_create,
            allowed_actions=_ACTION_KEYS,
        )
        hold_decision = normalize_binance_manager_hold_decision(
            response=response,
            actions=actions,
            symbols=[],
            allowed_actions=_ACTION_KEYS,
        )
        contract_error = binance_manager_response_contract_error(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        )
    else:
        blocks = _rows(prompt.get("blocks"))
        account = _mapping(prompt.get("account"))
        quotes = _quotes_by_symbol(prompt.get("quotes"))
        actions = sanitize_kis_manager_actions(
            response,
            blocks=blocks,
            quotes=quotes,
            account=account,
        )
        hold_decision = sanitize_kis_hold_decision(
            response.get("hold_decision"),
            action_count=sum(len(actions.get(key) or []) for key in _ACTION_KEYS),
            missed_upside_reviews=[],
        )
        contract_error = kis_manager_response_contract_error(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        )
    if contract_error:
        raise ValueError(f"wiki_shadow_manager_contract_error:{contract_error}")
    return {
        key: sorted(
            (dict(row) for row in actions.get(key, []) if isinstance(row, dict)),
            key=_canonical_json,
        )
        for key in _ACTION_KEYS
    }


def _action_delta(
    legacy: dict[str, Any],
    wiki: dict[str, list[dict[str, Any]]],
) -> tuple[str, ...]:
    delta: list[str] = []
    for key in _ACTION_KEYS:
        legacy_rows = legacy.get(key, []) if isinstance(legacy, dict) else []
        if canonical_payload_hash(legacy_rows) != canonical_payload_hash(wiki[key]):
            delta.append(key)
    return tuple(delta)


def _normalized_recorded_actions(
    value: Any,
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    if not isinstance(value, dict):
        return {key: [] for key in _ACTION_KEYS}, False
    actions: dict[str, list[dict[str, Any]]] = {}
    for key in _ACTION_KEYS:
        rows = value.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return {name: [] for name in _ACTION_KEYS}, False
        actions[key] = sorted(
            (dict(row) for row in rows),
            key=_canonical_json,
        )
    return actions, True


def _new_risk_actions(
    actions: dict[str, list[dict[str, Any]]],
    *,
    blocks: list[dict[str, Any]],
    venue: str,
) -> tuple[str, ...]:
    risks = [
        f"create:{str(row.get('symbol') or '').strip().lower()}"
        for row in actions.get("create_blocks", [])
    ]
    block_index = {
        str(row.get("block_id") or ""): row
        for row in blocks
        if str(row.get("block_id") or "")
    }
    for row in actions.get("update_blocks", []):
        block_id = str(row.get("block_id") or "")
        classifier = (
            kis_update_adds_new_risk
            if venue == "kis"
            else binance_update_adds_new_risk
        )
        if classifier(row, block_index.get(block_id, {})):
            risks.append(f"update:{block_id.lower()}")
    return tuple(sorted(set(risks)))


def _manager_enforcement_inputs_valid(
    prompt: dict[str, Any],
    *,
    venue: str,
) -> bool:
    decision_inputs = prompt.get("decision_inputs")
    if not isinstance(decision_inputs, list) or any(
        not isinstance(value, str) for value in decision_inputs
    ):
        return False
    required_inputs = {"account", "execution_gate", "blocks"}
    if venue == "binance":
        required_inputs.add("candidates")
    if not required_inputs.issubset(set(decision_inputs)):
        return False
    if not isinstance(prompt.get("account"), dict):
        return False
    if not isinstance(prompt.get("blocks"), list):
        return False
    if venue == "binance" and not isinstance(prompt.get("candidates"), list):
        return False
    if venue == "kis" and not isinstance(
        prompt.get("quotes"),
        (dict, list),
    ):
        return False
    return True


def _candidate_delta_from_actions(
    prompt: dict[str, Any],
    actions: dict[str, list[dict[str, Any]]],
    legacy_actions: dict[str, list[dict[str, Any]]],
    *,
    venue: str,
) -> tuple[tuple[str, ...], bool]:
    def collect(value: Any, rows: list[dict[str, Any]]) -> None:
        if isinstance(value, list):
            for child in value:
                collect(child, rows)
        elif isinstance(value, dict):
            if str(value.get("symbol") or value.get("ticker") or value.get("code") or "").strip():
                rows.append(dict(value))
            else:
                for child in value.values():
                    collect(child, rows)

    candidates: list[dict[str, Any]] = []
    if venue == "binance":
        candidates = _rows(prompt.get("candidates"))
    else:
        for key in (
            "candidates",
            "strategy",
            "aggressive_opportunities",
            "direct_daily_discovery",
            "daily_discovery",
            "pre_adoption_symbol_analysis",
        ):
            collect(prompt.get(key), candidates)
    normalized_candidates: list[dict[str, Any]] = []
    identity_by_id: dict[str, tuple[str, str, str]] = {}
    candidate_issues: list[str] = []
    for candidate in candidates:
        symbol = str(candidate.get("symbol") or candidate.get("ticker") or candidate.get("code") or "").strip().lower()
        market = str(candidate.get("market") or candidate.get("venue") or "").strip().lower()
        side = str(candidate.get("side") or candidate.get("position_side") or "").strip().lower()
        candidate_id = str(candidate.get("candidate_id") or "").strip().lower()
        explicit_candidate_id = bool(candidate_id)
        if not candidate_id:
            candidate_id = f"derived:{canonical_payload_hash({'venue': venue, 'symbol': symbol, 'market': market, 'side': side})[:24]}"
        identity = (symbol, market, side)
        previous = identity_by_id.get(candidate_id)
        if previous is not None:
            if explicit_candidate_id:
                candidate_issues.append(f"candidate_id_duplicate:{candidate_id}")
            elif previous == identity:
                continue
            if previous != identity:
                candidate_issues.append(f"candidate_id_conflict:{candidate_id}")
        identity_by_id[candidate_id] = identity
        normalized_candidates.append({**candidate, "candidate_id": candidate_id})
    candidates = normalized_candidates
    blocks = _rows(prompt.get("blocks"))
    block_index = {
        str(row.get("block_id") or ""): row
        for row in blocks
        if str(row.get("block_id") or "")
    }
    by_id = {
        str(row.get("candidate_id") or "").strip().lower(): row
        for row in candidates
        if str(row.get("candidate_id") or "").strip()
    }

    def refs(source: dict[str, list[dict[str, Any]]], label: str) -> tuple[set[str], list[str]]:
        resolved: set[str] = set()
        issues: list[str] = []
        for action_key in ("create_blocks", "update_blocks", "adopt_existing_blocks"):
            for index, row in enumerate(source.get(action_key, [])):
                explicit = str(
                    row.get("candidate_id")
                    or row.get("source_candidate_id")
                    or row.get("selected_candidate_id")
                    or ""
                ).strip().lower()
                if not explicit and action_key == "update_blocks":
                    current = block_index.get(str(row.get("block_id") or ""), {})
                    explicit = str(current.get("candidate_id") or "").strip().lower()
                if explicit:
                    if explicit in by_id:
                        action_symbol = str(row.get("symbol") or "").strip().lower()
                        action_market = str(row.get("market") or row.get("venue") or "").strip().lower()
                        action_side = str(row.get("side") or row.get("position_side") or "").strip().lower()
                        candidate_identity = identity_by_id[explicit]
                        contradiction = bool(
                            (action_symbol and candidate_identity[0] and action_symbol != candidate_identity[0])
                            or (action_market and candidate_identity[1] and action_market != candidate_identity[1])
                            or (action_side and candidate_identity[2] and action_side != candidate_identity[2])
                        )
                        if contradiction:
                            issues.append(f"candidate_ref_contradiction:{label}:{action_key}:{explicit}")
                        else:
                            resolved.add(explicit)
                    else:
                        issues.append(f"candidate_ref_missing:{label}:{action_key}:{explicit}")
                    continue
                symbol = str(row.get("symbol") or "").strip().lower()
                if not symbol and action_key == "update_blocks":
                    current = block_index.get(str(row.get("block_id") or ""), {})
                    symbol = str(current.get("symbol") or "").strip().lower()
                market = str(row.get("market") or row.get("venue") or "").strip().lower()
                side = str(row.get("side") or row.get("position_side") or "").strip().lower()
                matches = [
                    candidate
                    for candidate in candidates
                    if str(candidate.get("symbol") or candidate.get("ticker") or "").strip().lower() == symbol
                    and (
                        not market
                        or not str(candidate.get("market") or candidate.get("venue") or "").strip()
                        or str(candidate.get("market") or candidate.get("venue") or "").strip().lower() == market
                    )
                    and (
                        not side
                        or not str(candidate.get("side") or candidate.get("position_side") or "").strip()
                        or str(candidate.get("side") or candidate.get("position_side") or "").strip().lower() == side
                    )
                ]
                if len(matches) != 1:
                    kind = "ambiguous" if len(matches) > 1 else "missing"
                    issues.append(f"candidate_{kind}:{label}:{action_key}:{symbol or index}")
                    continue
                candidate_id = str(matches[0].get("candidate_id") or "").strip().lower()
                if not candidate_id:
                    issues.append(f"candidate_id_missing:{label}:{action_key}:{symbol or index}")
                    continue
                resolved.add(candidate_id)
        return resolved, issues

    wiki_refs, wiki_issues = refs(actions, "wiki")
    legacy_refs, legacy_issues = refs(legacy_actions, "legacy")
    delta = [*candidate_issues, *wiki_issues, *legacy_issues]
    delta.extend(f"candidate_ref_changed:{value}" for value in wiki_refs ^ legacy_refs)
    return tuple(sorted(set(delta))), not (candidate_issues or wiki_issues or legacy_issues)


def _selected_page_contract_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    page_required = {
        "page_id", "page_type", "scope", "title", "summary", "claims",
        "relationships", "status", "schema_version", "compiler_version",
    }
    claim_required = {"claim_id", "claim_type", "text", "status", "scope", "evidence"}
    evidence_required = {
        "evidence_id", "source_type", "source_id", "content_hash", "observed_at"
    }
    relationship_required = {"source_claim_id", "relationship_type", "target_id"}
    statuses = {"draft", "verified", "stale", "conflicted", "superseded", "rejected"}
    claim_types = {"fact", "interpretation", "hypothesis", "policy"}
    relationship_types = {
        "supports", "contradicts", "supersedes", "depends_on", "applies_to"
    }
    if not page_required.issubset(value):
        return False
    if (
        value.get("status") not in statuses
        or value.get("schema_version") != "jue_wiki_page_v3"
        or value.get("compiler_version") != "wiki_compiler_v1"
        or not isinstance(value.get("claims"), list)
        or not isinstance(value.get("relationships"), list)
    ):
        return False
    for claim in value["claims"]:
        if not isinstance(claim, dict) or not claim_required.issubset(claim):
            return False
        if (
            claim.get("claim_type") not in claim_types
            or claim.get("status") not in statuses
            or not isinstance(claim.get("evidence"), list)
        ):
            return False
        for evidence in claim["evidence"]:
            if not isinstance(evidence, dict) or not evidence_required.issubset(evidence):
                return False
            content_hash = str(evidence.get("content_hash") or "").strip().lower()
            if (
                len(content_hash) != 64
                or any(char not in "0123456789abcdef" for char in content_hash)
                or evidence.get("hash_origin", "source")
                not in {"source", "derived", "normalized_payload"}
                or any(not str(evidence.get(key) or "").strip() for key in evidence_required - {"content_hash"})
            ):
                return False
        if claim.get("status") == "verified" and not claim["evidence"]:
            return False
    for relationship in value["relationships"]:
        if not isinstance(relationship, dict) or not relationship_required.issubset(relationship):
            return False
        if relationship.get("relationship_type") not in relationship_types:
            return False
        if not str(relationship.get("source_claim_id") or "").strip() or not str(
            relationship.get("target_id") or ""
        ).strip():
            return False
    try:
        claims: list[WikiClaimV3] = []
        for claim_value in value.get("claims", []):
            if not isinstance(claim_value, dict):
                return False
            evidence = tuple(
                EvidenceRefV1(**evidence_value)
                for evidence_value in claim_value.get("evidence", [])
                if isinstance(evidence_value, dict)
            )
            if len(evidence) != len(claim_value.get("evidence", [])):
                return False
            claims.append(WikiClaimV3(**{**claim_value, "evidence": evidence}))
        relationships = tuple(
            WikiRelationshipV1(**relationship)
            for relationship in value.get("relationships", [])
            if isinstance(relationship, dict)
        )
        if len(relationships) != len(value.get("relationships", [])):
            return False
        JueWikiPageV3(
            **{
                **value,
                "claims": tuple(claims),
                "relationships": relationships,
            }
        )
    except (TypeError, ValueError):
        return False
    return True


def _snapshot_packet_valid(
    packet: Any,
    *,
    snapshot_id: str,
) -> bool:
    return bool(
        isinstance(packet, dict)
        and packet.get("version") == "wiki_context_packet_v1"
        and isinstance(packet.get("status"), str)
        and bool(str(packet.get("status") or "").strip())
        and packet.get("read_mode") in {"shadow", "prefer", "required"}
        and str(packet.get("snapshot_id") or "").strip() == snapshot_id
        and isinstance(packet.get("selected_pages"), list)
        and all(_selected_page_contract_valid(value) for value in packet["selected_pages"])
        and isinstance(packet.get("rejected_page_ids"), list)
        and all(isinstance(value, str) for value in packet["rejected_page_ids"])
        and isinstance(packet.get("coverage_status"), str)
        and bool(str(packet.get("coverage_status") or "").strip())
        and isinstance(packet.get("quality_warnings"), list)
        and all(isinstance(value, str) for value in packet["quality_warnings"])
        and type(packet.get("repair_required")) is bool
        and type(packet.get("char_count")) is int
        and packet["char_count"] >= 0
        and type(packet.get("required_eligible")) is bool
    )


def replay_shadow_record(
    recording: dict[str, Any],
    complete_json: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    completion_provenance: WikiCompletionProvenanceV1 | dict[str, Any] | None = None,
    completion_verifier: WikiCompletionSigner | None = None,
) -> WikiShadowComparisonV1:
    venue = str(recording.get("venue") or "").strip().lower()
    if venue not in _VENUES:
        raise ValueError("wiki_shadow_venue_invalid")
    run_id = str(recording.get("run_id") or "").strip()
    manager_input = recording.get("manager_input")
    if not run_id or not isinstance(manager_input, dict):
        raise ValueError("wiki_shadow_recording_invalid")
    snapshot_id = str(recording.get("wiki_snapshot_id") or "").strip()
    exact_input = json.loads(_canonical_json(manager_input))
    envelope = WikiRuntimePromptEnvelopeV1.from_dict(
        recording.get("wiki_runtime_prompt_envelope")
    )
    source_prompt = envelope.runtime_prompt()
    source_wiki_section = source_prompt.get("jue_wiki")
    source_packet = extract_wiki_context_packet(source_wiki_section)
    source_gate = source_prompt.get("jue_wiki_decision_gate")
    wiki_prompt = apply_jue_wiki_prompt_policy(
        source_prompt,
        target_read_mode="required",
        source_to_required=True,
    )
    strip_audit = wiki_prompt.get("jue_wiki_raw_rag_strip_audit")
    _removed_paths = tuple(
        str(value)
        for value in (
            strip_audit.get("removed_paths", [])
            if isinstance(strip_audit, dict)
            else []
        )
    )
    sanitized_prompt, remaining_raw_paths = strip_direct_raw_rag_context(wiki_prompt)
    if remaining_raw_paths:
        raise ValueError("wiki_shadow_target_prompt_raw_rag_residual")
    wiki_prompt = sanitized_prompt
    packet_wrapper = wiki_prompt.get("jue_wiki")
    packet = extract_wiki_context_packet(packet_wrapper)
    wiki_gate = wiki_prompt.get("jue_wiki_decision_gate")
    read_mode = (
        str(wiki_gate.get("read_mode") or "shadow")
        if isinstance(wiki_gate, dict)
        else "shadow"
    )
    envelope_valid = bool(
        envelope.venue == venue
        and envelope.legacy_manager_input_hash == canonical_payload_hash(exact_input)
        and envelope.wiki_runtime_prompt_hash == canonical_payload_hash(source_prompt)
        and isinstance(source_wiki_section, dict)
        and envelope.snapshot_trace_hash == canonical_payload_hash(source_packet)
        and isinstance(source_gate, dict)
        and envelope.gate_hash == canonical_payload_hash(source_gate)
        and (
            not str(source_gate.get("snapshot_id") or "").strip()
            or str(source_gate.get("snapshot_id") or "").strip() == snapshot_id
        )
        and isinstance(source_packet, dict)
        and str(recording.get("wiki_snapshot_trace_hash") or canonical_payload_hash(source_packet)).strip().lower()
        == canonical_payload_hash(source_packet)
        and isinstance(packet_wrapper, dict)
        and isinstance(packet, dict)
        and isinstance(wiki_gate, dict)
        and "jue_wiki_context" not in wiki_prompt
        and not remaining_raw_paths
    )
    context_valid = bool(
        envelope_valid
        and _snapshot_packet_valid(source_packet, snapshot_id=snapshot_id)
        and _snapshot_packet_valid(packet, snapshot_id=snapshot_id)
        and packet.get("read_mode") == "required"
        and packet.get("required_eligible") is True
        and wiki_gate.get("read_mode") == "required"
        and wiki_gate.get("allow_new_risk") is True
        and str(wiki_gate.get("snapshot_id") or "").strip() == snapshot_id
    )

    response = complete_json(wiki_prompt)
    if inspect.isawaitable(response):
        raise TypeError("wiki_shadow_completion_must_be_synchronous")
    if not isinstance(response, dict):
        raise ValueError("wiki_shadow_completion_must_be_object")
    provenance: WikiCompletionProvenanceV1 | None
    try:
        provenance = (
            completion_provenance
            if isinstance(completion_provenance, WikiCompletionProvenanceV1)
            else WikiCompletionProvenanceV1.from_dict(completion_provenance)
        )
    except (TypeError, ValueError):
        provenance = None
    recording_id = str(recording.get("recording_id") or "").strip()
    provenance_valid = bool(
        provenance is not None
        and completion_verifier is not None
        and recording_id
        and completion_verifier.verify(
            provenance,
            recording=recording,
            target_prompt=wiki_prompt,
            response=response,
        )
    )
    manager_contract_valid = True
    try:
        actions = _validated_actions(venue=venue, prompt=wiki_prompt, response=response)
    except ValueError as exc:
        if not str(exc).startswith("wiki_shadow_manager_contract_error:"):
            raise
        manager_contract_valid = False
        actions = {
            key: sorted(_rows(response.get(key)), key=_canonical_json)
            for key in _ACTION_KEYS
        }
    proposed_actions = actions
    legacy_actions, legacy_actions_valid = _normalized_recorded_actions(
        recording.get("legacy_actions")
    )
    blocks = _rows(exact_input.get("blocks"))
    local_blockers, execution_allowed, new_risk_allowed, local_safety_valid = (
        _local_safety_state(venue, exact_input)
    )
    wiki_local_blockers, wiki_execution_allowed, wiki_new_risk_allowed, wiki_safety_valid = (
        _local_safety_state(venue, wiki_prompt)
    )
    allow_create, allow_scale = _production_safety_permissions(venue, wiki_prompt)
    actions, production_suppressed_count = _apply_production_safety_permissions(
        actions,
        venue=venue,
        blocks=blocks,
        allow_create=allow_create,
        allow_scale=allow_scale,
    )
    production_blocked_proposal = production_suppressed_count > 0
    if isinstance(wiki_gate, dict):
        suppress_wiki = (
            apply_kis_wiki_decision_gate
            if venue == "kis"
            else apply_binance_wiki_decision_gate
        )
        actions, _normal_suppression_audit = suppress_wiki(
            actions,
            wiki_gate,
            trusted_read_mode=read_mode,
            current_blocks=blocks,
        )
    try:
        legacy_summary = ManagerSafetySummaryV1.from_dict(
            recording.get("legacy_safety_summary")
        )
    except ValueError:
        legacy_summary = None
    summary_valid = bool(
        legacy_summary is not None
        and legacy_summary.venue == venue
        and legacy_summary.manager_input_hash == canonical_payload_hash(exact_input)
        and legacy_summary.blockers == local_blockers
        and legacy_summary.execution_allowed is execution_allowed
        and legacy_summary.new_risk_allowed is new_risk_allowed
        and legacy_summary.section_hashes == _safety_section_hashes(exact_input)
    )
    wiki_blockers = set(wiki_local_blockers)
    if isinstance(wiki_gate, dict) and wiki_gate.get("allow_new_risk") is False:
        wiki_blockers.add("wiki_required_gate")
    wiki_summary = ManagerSafetySummaryV1(
        venue=venue,  # type: ignore[arg-type]
        manager_input_hash=canonical_payload_hash(wiki_prompt),
        blockers=tuple(sorted(wiki_blockers)),
        execution_allowed=wiki_execution_allowed,
        new_risk_allowed=bool(
            wiki_new_risk_allowed
            and isinstance(wiki_gate, dict)
            and wiki_gate.get("allow_new_risk") is not False
        ),
        section_hashes=_safety_section_hashes(wiki_prompt),
    )
    legacy_summary_hash = (
        canonical_payload_hash(legacy_summary.to_dict())
        if legacy_summary is not None
        else ""
    )
    wiki_summary_hash = canonical_payload_hash(wiki_summary.to_dict())
    safety_gate_loss = (
        tuple(sorted(set(legacy_summary.blockers) - wiki_blockers))
        if summary_valid and legacy_summary is not None
        else ("safety_summary_mismatch",)
    )
    safety_gate_delta = (
        tuple(sorted(set(legacy_summary.blockers) ^ wiki_blockers))
        if summary_valid and legacy_summary is not None
        else ("safety_summary_mismatch",)
    )
    if production_blocked_proposal:
        safety_gate_loss = tuple(
            sorted(set(safety_gate_loss) | {"server_blocked_new_risk_proposal"})
        )
        safety_gate_delta = tuple(
            sorted(set(safety_gate_delta) | {"server_blocked_new_risk_proposal"})
        )
    if not provenance_valid:
        provenance_reason = (
            "completion_provenance_missing"
            if completion_provenance is None
            else "completion_provenance_mismatch"
        )
        safety_gate_loss = tuple(
            sorted(set(safety_gate_loss) | {provenance_reason})
        )
        safety_gate_delta = tuple(
            sorted(set(safety_gate_delta) | {provenance_reason})
        )
    if not manager_contract_valid:
        safety_gate_loss = tuple(
            sorted(set(safety_gate_loss) | {"manager_contract_error"})
        )
        safety_gate_delta = tuple(
            sorted(set(safety_gate_delta) | {"manager_contract_error"})
        )
    enforcement_valid = bool(
        local_safety_valid
        and wiki_safety_valid
        and manager_contract_valid
        and summary_valid
        and envelope_valid
        and _manager_enforcement_inputs_valid(exact_input, venue=venue)
        and _manager_enforcement_inputs_valid(wiki_prompt, venue=venue)
    )
    if not enforcement_valid and not summary_valid:
        safety_gate_loss = tuple(
            sorted(set(safety_gate_loss) | {"enforcement_inputs_missing"})
        )
    candidate_delta, candidate_trace_valid = _candidate_delta_from_actions(
        exact_input,
        proposed_actions,
        legacy_actions,
        venue=venue,
    )
    action_delta = _action_delta(legacy_actions, actions)
    outage_value = recording.get("simulate_wiki_outage")
    outage_valid = type(outage_value) is bool
    outage = outage_value if outage_valid else False
    outage_prompt = json.loads(_canonical_json(wiki_prompt))
    outage_filtered_actions = actions
    if outage:
        outage_prompt.pop("jue_wiki_context", None)
        outage_prompt["jue_wiki_decision_gate"] = {
            "allow_new_risk": False,
            "allow_exit_actions": True,
            "reason": "wiki_required_repository_unavailable",
            "read_mode": "required",
            "snapshot_id": "",
            "version": "wiki_decision_gate_v1",
        }
        suppress = (
            apply_kis_wiki_decision_gate
            if venue == "kis"
            else apply_binance_wiki_decision_gate
        )
        outage_filtered_actions, _outage_audit = suppress(
            actions,
            outage_prompt["jue_wiki_decision_gate"],
            trusted_read_mode="required",
            current_blocks=blocks,
        )
    post_outage_new_risk = _new_risk_actions(
        outage_filtered_actions,
        blocks=blocks,
        venue=venue,
    )
    wiki_new_risk = bool(outage and post_outage_new_risk)
    snapshot_trace_complete = bool(context_valid and not remaining_raw_paths)
    complete = bool(
        snapshot_id
        and snapshot_trace_complete
        and enforcement_valid
        and legacy_actions_valid
        and candidate_trace_valid
        and outage_valid
        and outage is True
        and not remaining_raw_paths
        and provenance_valid
    )
    return WikiShadowComparisonV1(
        run_id=run_id,
        venue=venue,  # type: ignore[arg-type]
        legacy_prompt_hash=canonical_payload_hash(exact_input),
        wiki_prompt_hash=canonical_payload_hash(wiki_prompt),
        snapshot_id=snapshot_id,
        legacy_action_hash=canonical_payload_hash(legacy_actions),
        wiki_action_hash=canonical_payload_hash(actions),
        safety_gate_loss=safety_gate_loss,
        direct_raw_rag_paths=remaining_raw_paths,
        comparison_status="complete" if complete else "incomplete",
        created_at=datetime.now(timezone.utc).isoformat(),
        snapshot_trace_complete=snapshot_trace_complete,
        wiki_induced_new_risk_expansion=outage and wiki_new_risk,
        simulated_wiki_outage=outage,
        wiki_read_mode=read_mode,
        candidate_delta=candidate_delta,
        action_delta=action_delta,
        safety_gate_delta=safety_gate_delta,
        removed_raw_rag_paths=tuple(sorted(set(_removed_paths))),
        legacy_safety_summary_hash=legacy_summary_hash,
        wiki_safety_summary_hash=wiki_summary_hash,
        completion_provenance_hash=(
            provenance.provenance_hash if provenance is not None else ""
        ),
        completion_provenance_verified=provenance_valid,
        recording_id=recording_id,
        completion_provenance_json=(
            _canonical_json(provenance.to_dict()) if provenance is not None else ""
        ),
    )
