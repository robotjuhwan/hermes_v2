from __future__ import annotations

import pytest

from tradecraft.services.jue_codex_patch_workspace import validate_patch_paths
from tradecraft.services.jue_codex_verifier import JueCodexVerifier


def test_validate_patch_paths_rejects_blocked_env_path() -> None:
    result = validate_patch_paths(
        touched_paths=[".env"],
        allowed_paths=["src/tradecraft/services", "tests"],
        blocked_paths=[".env", ".runtime"],
    )

    assert result == {
        "status": "rejected",
        "reason": "blocked_path_touched",
        "blocked_matches": [".env"],
    }


def test_validate_patch_paths_block_prefix_is_boundary_aware() -> None:
    result = validate_patch_paths(
        touched_paths=[".env.example"],
        allowed_paths=[".env.example"],
        blocked_paths=[".env"],
    )

    assert result == {"status": "ok"}


def test_validate_patch_paths_block_glob_matches_env_local() -> None:
    result = validate_patch_paths(
        touched_paths=[".env.local"],
        allowed_paths=[".env.local"],
        blocked_paths=[".env.*"],
    )

    assert result == {
        "status": "rejected",
        "reason": "blocked_path_touched",
        "blocked_matches": [".env.local"],
    }


def test_validate_patch_paths_directory_policy_does_not_match_similar_prefix() -> None:
    allowed_result = validate_patch_paths(
        touched_paths=["secretsauce"],
        allowed_paths=["secretsauce"],
        blocked_paths=["secrets"],
    )
    blocked_result = validate_patch_paths(
        touched_paths=["secrets/token"],
        allowed_paths=["secrets"],
        blocked_paths=["secrets"],
    )

    assert allowed_result == {"status": "ok"}
    assert blocked_result == {
        "status": "rejected",
        "reason": "blocked_path_touched",
        "blocked_matches": ["secrets/token"],
    }


def test_validate_patch_paths_rejects_invalid_policy_path() -> None:
    result = validate_patch_paths(
        touched_paths=["src/tradecraft/services/jue_codex_verifier.py"],
        allowed_paths=["src/tradecraft/services"],
        blocked_paths=["../secrets"],
    )

    assert result == {
        "status": "rejected",
        "reason": "invalid_policy_path",
        "invalid_policy_paths": [
            {"path": "../secrets", "policy": "blocked_paths", "reason": "path_traversal"}
        ],
    }


def test_validate_patch_paths_accepts_allowed_source_and_test_paths() -> None:
    result = validate_patch_paths(
        touched_paths=[
            "src/tradecraft/services/jue_codex_verifier.py",
            "tests/test_jue_codex_verifier.py",
        ],
        allowed_paths=["src/tradecraft/services", "tests"],
        blocked_paths=[".env", ".runtime"],
    )

    assert result == {"status": "ok"}


def test_validate_patch_paths_rejects_outside_allowed_path() -> None:
    result = validate_patch_paths(
        touched_paths=["src/tradecraft/main.py"],
        allowed_paths=["src/tradecraft/services", "tests"],
        blocked_paths=[".env", ".runtime"],
    )

    assert result == {
        "status": "rejected",
        "reason": "outside_allowed_paths",
        "outside_allowed": ["src/tradecraft/main.py"],
    }


def test_validate_patch_paths_rejects_traversal_and_absolute_paths() -> None:
    traversal_result = validate_patch_paths(
        touched_paths=["../secrets.txt"],
        allowed_paths=["src/tradecraft/services", "tests"],
        blocked_paths=[".env", ".runtime"],
    )
    absolute_result = validate_patch_paths(
        touched_paths=["/tmp/secrets.txt"],
        allowed_paths=["src/tradecraft/services", "tests"],
        blocked_paths=[".env", ".runtime"],
    )

    assert traversal_result["status"] == "rejected"
    assert traversal_result["reason"] == "invalid_path"
    assert traversal_result["invalid_paths"] == [
        {"path": "../secrets.txt", "reason": "path_traversal"}
    ]
    assert absolute_result["status"] == "rejected"
    assert absolute_result["reason"] == "invalid_path"
    assert absolute_result["invalid_paths"] == [
        {"path": "/tmp/secrets.txt", "reason": "absolute_path"}
    ]


def test_verifier_pass_command_captures_output(tmp_path) -> None:
    test_file = tmp_path / "test_pass.py"
    test_file.write_text("def test_prints_123():\n    print('123')\n", encoding="utf-8")

    result = JueCodexVerifier(tmp_path).run_commands(["pytest -s test_pass.py"])

    assert result["status"] == "pass"
    assert len(result["results"]) == 1
    assert result["results"][0]["command"] == "pytest -s test_pass.py"
    assert result["results"][0]["status"] == "pass"
    assert result["results"][0]["returncode"] == 0
    assert "123" in result["results"][0]["output_excerpt"]
    assert result["results"][0]["elapsed_sec"] >= 0


def test_verifier_fail_command_stops_subsequent_commands(tmp_path) -> None:
    test_file = tmp_path / "test_after.py"
    test_file.write_text("def test_after():\n    assert True\n", encoding="utf-8")

    result = JueCodexVerifier(tmp_path).run_commands(
        ["python3 -m py_compile missing.py", "pytest test_after.py"]
    )

    assert result["status"] == "fail"
    assert len(result["results"]) == 1
    assert result["results"][0]["status"] == "fail"
    assert result["results"][0]["returncode"] != 0
    assert result["results"][0]["command"] == "python3 -m py_compile missing.py"


@pytest.mark.parametrize(
    "command",
    [
        "curl https://example.com",
        "pytest tests; curl https://example.com",
        "pytest tests && curl https://example.com",
        "pytest tests | cat",
        "pytest tests > out.txt",
        "pytest $(printf tests)",
        "pytest `printf tests`",
        "A=1 pytest tests",
        "python -c 'print(123)'",
        "/tmp/pytest tests",
        "./pytest tests",
        "tools/python3 -m py_compile x.py",
    ],
)
def test_verifier_rejects_unsafe_or_unallowed_commands(tmp_path, command) -> None:
    result = JueCodexVerifier(tmp_path).run_commands([command])

    assert result["status"] == "fail"
    assert result["results"] == [
        {
            "command": command,
            "status": "rejected",
            "returncode": None,
            "message": "Command is not allowed for verification.",
            "output_excerpt": "",
            "elapsed_sec": 0.0,
        }
    ]


def test_verifier_timeout_returns_failure_result(tmp_path) -> None:
    test_file = tmp_path / "test_sleep.py"
    test_file.write_text(
        "import time\n\ndef test_sleeps():\n    time.sleep(1)\n",
        encoding="utf-8",
    )

    result = JueCodexVerifier(tmp_path, timeout_sec=0.1).run_commands(
        ["pytest test_sleep.py"]
    )

    assert result["status"] == "fail"
    assert len(result["results"]) == 1
    assert result["results"][0]["command"] == "pytest test_sleep.py"
    assert result["results"][0]["status"] == "timeout"
    assert result["results"][0]["returncode"] is None
    assert "message" in result["results"][0]
    assert "timed out" in result["results"][0]["message"]
    assert "timed out" in result["results"][0]["output_excerpt"]


def test_verifier_output_excerpt_is_capped(tmp_path) -> None:
    test_file = tmp_path / "test_output.py"
    test_file.write_text(
        "def test_output():\n"
        "    print('EARLY_MARKER')\n"
        "    print('x' * 20000)\n"
        "    print('TAIL_MARKER')\n",
        encoding="utf-8",
    )

    result = JueCodexVerifier(tmp_path).run_commands(["pytest -s test_output.py"])

    assert result["status"] == "pass"
    output_excerpt = result["results"][0]["output_excerpt"]
    assert result["results"][0]["output_truncated"] is True
    assert len(output_excerpt) <= 4000
    assert "EARLY_MARKER" not in output_excerpt
    assert "TAIL_MARKER" in output_excerpt


def test_verifier_marks_truncated_when_retained_output_exceeds_excerpt_cap(
    tmp_path,
) -> None:
    test_file = tmp_path / "test_output_5000.py"
    test_file.write_text(
        "def test_output():\n    print('a' * 5000)\n",
        encoding="utf-8",
    )

    result = JueCodexVerifier(tmp_path).run_commands(["pytest -s test_output_5000.py"])

    assert result["status"] == "pass"
    assert result["results"][0]["output_truncated"] is True
    assert len(result["results"][0]["output_excerpt"]) <= 4000


def test_verifier_empty_command_list_passes(tmp_path) -> None:
    result = JueCodexVerifier(tmp_path).run_commands([])

    assert result == {"status": "pass", "results": []}
