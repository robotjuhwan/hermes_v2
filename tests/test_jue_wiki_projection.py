from __future__ import annotations

import errno
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from tradecraft.services.jue_wiki_contract import (
    JueWikiPageV3,
    WikiClaimV3,
    WikiRelationshipV1,
    WikiSnapshotV1,
)
from tradecraft.services.jue_wiki_projection import JueWikiProjectionWriter
from tradecraft.services.jue_wiki_projection import (
    GENERATION_MARKER,
    WikiProjectionError,
    WikiProjectionRecoveryError,
)


def _snapshot(
    *,
    snapshot_id: str = "snapshot:kis:1",
    text: str = "Revision direction is positive.",
) -> WikiSnapshotV1:
    claim = WikiClaimV3(
        claim_id="claim:kis:005930:direction",
        claim_type="interpretation",
        text=text,
        status="draft",
        scope="kis",
        evidence=(),
        symbols=("005930",),
        confidence=0.4,
    )
    page = JueWikiPageV3(
        page_id="kis.symbol.005930",
        page_type="symbol",
        scope="kis",
        title="005930",
        summary="Positive revision direction.",
        claims=(claim,),
        relationships=(
            WikiRelationshipV1(
                source_claim_id=claim.claim_id,
                relationship_type="applies_to",
                target_id="005930",
            ),
        ),
        status="draft",
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
    )
    return WikiSnapshotV1(
        snapshot_id=snapshot_id,
        scope="kis",
        candidate_artifact_ids=(),
        pages=(page,),
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
        created_at="2026-07-11T00:00:00+00:00",
    )


def test_index_rebuild_is_equivalent(tmp_path: Path) -> None:
    published_snapshot = _snapshot()
    writer = JueWikiProjectionWriter(tmp_path / "projection")

    first = writer.project(published_snapshot)
    first_target = os.readlink(writer.projection_root)
    second = writer.rebuild_index(published_snapshot)

    assert second.row_hashes == first.row_hashes
    with sqlite3.connect(writer.index_path) as conn:
        stored_hashes = tuple(
            row[0]
            for row in conn.execute(
                "SELECT row_hash FROM wiki_search_rows ORDER BY rowid"
            )
        )
    assert second.row_hashes == stored_hashes
    assert writer.projection_root.is_symlink()
    assert os.readlink(writer.projection_root) != first_target
    assert not (tmp_path / first_target).exists()


def test_projection_renders_structured_markdown_and_search_rows(tmp_path: Path) -> None:
    snapshot = _snapshot()
    writer = JueWikiProjectionWriter(tmp_path / "projection")

    result = writer.project(snapshot)

    index_markdown = (writer.projection_root / "index.md").read_text()
    contradictions = (writer.projection_root / "contradictions.md").read_text()
    page_markdown = next((writer.projection_root / "pages").iterdir()).read_text()
    assert snapshot.snapshot_id in index_markdown
    assert "No contradictions." in contradictions
    assert snapshot.pages[0].claims[0].text in page_markdown
    assert "applies_to" in page_markdown
    with sqlite3.connect(writer.index_path) as conn:
        rows = conn.execute(
            "SELECT page_id, claim_id, title, body FROM wiki_search"
        ).fetchall()
    assert rows == [
        (
            "kis.symbol.005930",
            "claim:kis:005930:direction",
            "005930",
            "Revision direction is positive.",
        )
    ]
    assert len(result.row_hashes) == 1


def test_projection_failure_leaves_previous_projection_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    original = _snapshot()
    writer.project(original)
    original_index = (writer.projection_root / "index.md").read_bytes()
    original_hashes = writer.rebuild_index(original).row_hashes

    def fail_render(page: JueWikiPageV3) -> str:
        raise RuntimeError(page.page_id)

    monkeypatch.setattr(writer, "_render_page", fail_render)
    with pytest.raises(RuntimeError, match="kis.symbol.005930"):
        writer.project(
            _snapshot(snapshot_id="snapshot:kis:2", text="Changed projection.")
        )

    assert (writer.projection_root / "index.md").read_bytes() == original_index
    with sqlite3.connect(writer.index_path) as conn:
        stored_hashes = tuple(
            row[0]
            for row in conn.execute(
                "SELECT row_hash FROM wiki_search_rows ORDER BY rowid"
            )
        )
    assert stored_hashes == original_hashes


def test_atomic_promotion_failure_restores_previous_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    original = _snapshot()
    writer.project(original)
    original_index = (writer.projection_root / "index.md").read_bytes()
    original_target = os.readlink(writer.projection_root)
    real_replace = os.replace
    failed = False

    def fail_first_promotion(
        source: str | Path,
        destination: str | Path,
        **kwargs: object,
    ) -> None:
        nonlocal failed
        source_path = Path(source)
        destination_path = Path(destination)
        is_promotion = (
            source_path.name.startswith(".projection.pointer-")
            and destination_path.name == writer.projection_root.name
        )
        if is_promotion and not failed:
            failed = True
            raise OSError("promotion failed")
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(
        "tradecraft.services.jue_wiki_projection.os.replace",
        fail_first_promotion,
    )
    with pytest.raises(OSError, match="promotion failed"):
        writer.project(_snapshot(snapshot_id="snapshot:kis:2"))

    assert failed is True
    assert os.readlink(writer.projection_root) == original_target
    assert (writer.projection_root / "index.md").read_bytes() == original_index


def test_parent_fsync_failure_after_swap_rolls_back_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    writer.project(_snapshot())
    original_target = os.readlink(writer.projection_root)
    original_index = (writer.projection_root / "index.md").read_bytes()
    from tradecraft.services import jue_wiki_projection as projection_module

    real_replace = os.replace
    real_fsync = os.fsync
    promoted = False
    failed = False

    def observe_promotion(
        source: str | Path,
        destination: str | Path,
        **kwargs: object,
    ) -> None:
        nonlocal promoted
        real_replace(source, destination, **kwargs)
        if Path(source).name.startswith(".projection.pointer-"):
            promoted = True

    def fail_parent_once(descriptor: int) -> None:
        nonlocal failed
        if promoted and not failed:
            failed = True
            raise OSError("parent fsync failed")
        real_fsync(descriptor)

    monkeypatch.setattr(projection_module.os, "replace", observe_promotion)
    monkeypatch.setattr(projection_module.os, "fsync", fail_parent_once)

    with pytest.raises(OSError, match="parent fsync failed"):
        writer.project(_snapshot(snapshot_id="snapshot:kis:2", text="changed"))

    assert os.readlink(writer.projection_root) == original_target
    assert (writer.projection_root / "index.md").read_bytes() == original_index
    assert len(tuple(tmp_path.glob(".projection.generation-*"))) == 1


def test_rollback_failure_preserves_old_and_new_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    writer.project(_snapshot())
    old_target = os.readlink(writer.projection_root)
    old_generation = tmp_path / old_target
    from tradecraft.services import jue_wiki_projection as projection_module

    real_replace = os.replace
    real_fsync = os.fsync
    promoted = False
    fsync_failed = False

    def fail_parent_once(descriptor: int) -> None:
        nonlocal fsync_failed
        if promoted and not fsync_failed:
            fsync_failed = True
            raise OSError("parent fsync failed")
        real_fsync(descriptor)

    def fail_rollback(
        source: str | Path,
        destination: str | Path,
        **kwargs: object,
    ) -> None:
        nonlocal promoted
        if Path(source).name.startswith(".projection.rollback-"):
            raise OSError("rollback replace failed")
        real_replace(source, destination, **kwargs)
        if Path(source).name.startswith(".projection.pointer-"):
            promoted = True

    monkeypatch.setattr(projection_module.os, "fsync", fail_parent_once)
    monkeypatch.setattr(projection_module.os, "replace", fail_rollback)

    with pytest.raises(WikiProjectionRecoveryError, match="rollback_failed"):
        writer.project(_snapshot(snapshot_id="snapshot:kis:2", text="changed"))

    generations = tuple(tmp_path.glob(".projection.generation-*"))
    assert old_generation in generations
    assert len(generations) == 2
    assert writer.index_path.is_file()


def test_unmanaged_projection_directory_is_refused_without_changes(
    tmp_path: Path,
) -> None:
    projection_root = tmp_path / "projection"
    projection_root.mkdir()
    marker = projection_root / "keep.txt"
    marker.write_text("unmanaged")
    writer = JueWikiProjectionWriter(projection_root)

    with pytest.raises(WikiProjectionError, match="projection_root_unmanaged"):
        writer.project(_snapshot())

    assert marker.read_text() == "unmanaged"
    assert not projection_root.is_symlink()


def test_arbitrary_old_symlink_target_is_never_deleted(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "keep.txt"
    marker.write_text("external")
    projection_root = tmp_path / "projection"
    projection_root.symlink_to(external.name, target_is_directory=True)
    writer = JueWikiProjectionWriter(projection_root)

    writer.project(_snapshot())

    assert marker.read_text() == "external"
    assert projection_root.is_symlink()
    assert os.readlink(projection_root).startswith(".projection.generation-")


def test_rebuild_failure_keeps_prior_live_index_queryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    snapshot = _snapshot()
    writer.project(snapshot)
    original_target = os.readlink(writer.projection_root)
    with sqlite3.connect(writer.index_path) as conn:
        original_rows = conn.execute(
            "SELECT claim_id, body FROM wiki_search"
        ).fetchall()

    def fail_index(index_path: Path, snapshot: WikiSnapshotV1) -> tuple[str, ...]:
        raise RuntimeError(f"index failure:{index_path.name}:{snapshot.snapshot_id}")

    monkeypatch.setattr(writer, "_build_index", fail_index)
    with pytest.raises(RuntimeError, match="index failure"):
        writer.rebuild_index(snapshot)

    assert os.readlink(writer.projection_root) == original_target
    with sqlite3.connect(writer.index_path) as conn:
        assert conn.execute("SELECT claim_id, body FROM wiki_search").fetchall() == original_rows


def test_post_commit_cleanup_failure_returns_success_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    writer.project(_snapshot())
    old_target = os.readlink(writer.projection_root)
    old_generation = tmp_path / old_target
    real_remove = writer._remove_owned_generation_at

    def fail_old_cleanup(
        parent_fd: int,
        name: str,
        *,
        require_marker: bool,
    ) -> bool:
        if require_marker:
            raise OSError("cleanup failed")
        return real_remove(parent_fd, name, require_marker=require_marker)

    monkeypatch.setattr(writer, "_remove_owned_generation_at", fail_old_cleanup)

    result = writer.project(
        _snapshot(snapshot_id="snapshot:kis:2", text="Committed new text.")
    )

    assert result.cleanup_warnings == ("old_generation_cleanup_failed",)
    assert old_generation.is_dir()
    assert os.readlink(writer.projection_root) != old_target
    with sqlite3.connect(writer.index_path) as conn:
        assert conn.execute("SELECT body FROM wiki_search").fetchall() == [
            ("Committed new text.",)
        ]


def test_fts_verification_rejects_manifest_mapping_mismatch(tmp_path: Path) -> None:
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    writer.project(_snapshot())
    with sqlite3.connect(writer.index_path) as conn:
        conn.execute("UPDATE wiki_search SET body = 'corrupted body'")
        conn.commit()

        with pytest.raises(
            WikiProjectionError,
            match="wiki_search_index_verification_failed",
        ):
            writer._verified_index_hashes(conn)


@pytest.mark.parametrize(
    ("generation_name", "marker_payload"),
    [
        (".projection.generation-malformed", "{not-json"),
        (
            ".projection.generation-wrong-version",
            '{"schema_version":"wrong","projection_name":"projection",'
            '"snapshot_id":"snapshot:kis:1"}',
        ),
        (
            ".projection.generation-wrong-name",
            '{"schema_version":"jue_wiki_projection_v1",'
            '"projection_name":"other","snapshot_id":"snapshot:kis:1"}',
        ),
        (
            ".projection.generation-empty-snapshot",
            '{"schema_version":"jue_wiki_projection_v1",'
            '"projection_name":"projection","snapshot_id":""}',
        ),
        (
            ".projection.generation-extra-field",
            '{"schema_version":"jue_wiki_projection_v1",'
            '"projection_name":"projection","snapshot_id":"snapshot:kis:1",'
            '"unexpected":true}',
        ),
        (
            "not-writer-owned",
            '{"schema_version":"jue_wiki_projection_v1",'
            '"projection_name":"projection","snapshot_id":"snapshot:kis:1"}',
        ),
    ],
)
def test_invalid_generation_markers_are_never_deleted(
    tmp_path: Path,
    generation_name: str,
    marker_payload: str,
) -> None:
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    generation = tmp_path / generation_name
    generation.mkdir()
    (generation / GENERATION_MARKER).write_text(marker_payload)

    writer._remove_owned_generation(generation, require_marker=True)

    assert generation.is_dir()


def test_projection_uses_safe_page_filenames_and_keeps_siblings(tmp_path: Path) -> None:
    sibling = tmp_path / "keep.txt"
    sibling.write_text("do not delete")
    snapshot = _snapshot()
    unsafe_page = JueWikiPageV3(
        page_id="../../keep.txt",
        page_type=snapshot.pages[0].page_type,
        scope=snapshot.pages[0].scope,
        title=snapshot.pages[0].title,
        summary=snapshot.pages[0].summary,
        claims=snapshot.pages[0].claims,
        relationships=snapshot.pages[0].relationships,
        status=snapshot.pages[0].status,
        schema_version=snapshot.pages[0].schema_version,
        compiler_version=snapshot.pages[0].compiler_version,
    )
    unsafe_snapshot = WikiSnapshotV1(
        snapshot_id=snapshot.snapshot_id,
        scope=snapshot.scope,
        candidate_artifact_ids=snapshot.candidate_artifact_ids,
        pages=(unsafe_page,),
        schema_version=snapshot.schema_version,
        compiler_version=snapshot.compiler_version,
        created_at=snapshot.created_at,
    )
    writer = JueWikiProjectionWriter(tmp_path / "projection")

    writer.project(unsafe_snapshot)

    assert sibling.read_text() == "do not delete"
    assert len(tuple((writer.projection_root / "pages").iterdir())) == 1


def test_non_containment_caller_supports_symlinked_parent_alias(
    tmp_path: Path,
) -> None:
    actual_parent = tmp_path / "actual"
    actual_parent.mkdir()
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(actual_parent, target_is_directory=True)
    writer = JueWikiProjectionWriter(alias_parent / "projection")

    writer.project(_snapshot())

    assert writer.projection_root.is_symlink()
    assert (actual_parent / "projection").is_symlink()


def test_containment_rejects_parent_replaced_before_project(
    tmp_path: Path,
) -> None:
    containment = tmp_path / "runtime"
    v3_root = containment / ".v3"
    v3_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("outside")
    writer = JueWikiProjectionWriter(
        v3_root / "projection",
        containment_root=containment,
    )
    v3_root.rmdir()
    v3_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        WikiProjectionError,
        match="projection_containment_invalid",
    ):
        writer.project(_snapshot())

    assert marker.read_text() == "outside"
    assert tuple(outside.iterdir()) == (marker,)


def test_containment_creates_missing_managed_parent_components(
    tmp_path: Path,
) -> None:
    containment = tmp_path / "runtime"
    containment.mkdir()
    projection_root = containment / ".v3" / "wiki" / "projection"
    writer = JueWikiProjectionWriter(
        projection_root,
        containment_root=containment,
    )

    writer.project(_snapshot())

    assert projection_root.is_symlink()
    assert not (containment / ".v3").is_symlink()
    assert not (containment / ".v3" / "wiki").is_symlink()


def test_containment_rechecks_after_build_before_pointer_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    containment = tmp_path / "runtime"
    v3_root = containment / ".v3"
    v3_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("outside")
    writer = JueWikiProjectionWriter(
        v3_root / "projection",
        containment_root=containment,
    )
    writer.project(_snapshot())
    original_target = os.readlink(writer.projection_root)
    original_index = (writer.projection_root / "index.md").read_bytes()
    original_v3 = containment / ".v3-original"
    real_build = writer._build_projection

    def build_then_swap_parent(
        parent_fd: int,
        generation_fd: int,
        snapshot: WikiSnapshotV1,
    ) -> tuple[str, ...]:
        row_hashes = real_build(parent_fd, generation_fd, snapshot)
        v3_root.rename(original_v3)
        v3_root.symlink_to(outside, target_is_directory=True)
        return row_hashes

    monkeypatch.setattr(writer, "_build_projection", build_then_swap_parent)

    writer.project(_snapshot(snapshot_id="snapshot:kis:2", text="changed"))

    assert marker.read_text() == "outside"
    assert tuple(outside.iterdir()) == (marker,)
    v3_root.unlink()
    original_v3.rename(v3_root)
    assert os.readlink(writer.projection_root) != original_target
    assert (writer.projection_root / "index.md").read_bytes() != original_index


@pytest.mark.parametrize("operation", ["project", "rebuild"])
def test_pinned_parent_fd_closes_pointer_swap_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    containment = tmp_path / "runtime"
    v3_root = containment / ".v3"
    v3_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("outside")
    writer = JueWikiProjectionWriter(
        v3_root / "projection",
        containment_root=containment,
    )
    writer.project(_snapshot())
    detached_v3 = containment / ".v3-detached"
    real_replace = os.replace
    swapped = False

    def swap_parent_inside_replace(
        source: str | Path,
        destination: str | Path,
        **kwargs: object,
    ) -> None:
        nonlocal swapped
        if not swapped and Path(source).name.startswith(".projection.pointer-"):
            v3_root.rename(detached_v3)
            v3_root.symlink_to(outside, target_is_directory=True)
            (outside / Path(source).name).symlink_to("attacker-generation")
            (outside / "projection").symlink_to("outside-old")
            swapped = True
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(
        "tradecraft.services.jue_wiki_projection.os.replace",
        swap_parent_inside_replace,
    )
    changed = _snapshot(snapshot_id="snapshot:kis:2", text="changed")
    if operation == "project":
        writer.project(changed)
    else:
        writer.rebuild_index(changed)

    assert swapped is True
    assert os.readlink(outside / "projection") == "outside-old"
    v3_root.unlink()
    detached_v3.rename(v3_root)
    with sqlite3.connect(writer.index_path) as conn:
        assert conn.execute("SELECT body FROM wiki_search").fetchall() == [
            ("changed",)
        ]


def test_pinned_parent_fd_closes_rollback_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    containment = tmp_path / "runtime"
    v3_root = containment / ".v3"
    v3_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("outside")
    writer = JueWikiProjectionWriter(
        v3_root / "projection",
        containment_root=containment,
    )
    writer.project(_snapshot())
    old_target = os.readlink(writer.projection_root)
    detached_v3 = containment / ".v3-detached"
    from tradecraft.services import jue_wiki_projection as projection_module

    real_replace = os.replace
    real_fsync = os.fsync
    promoted = False
    failed = False

    def observe_promotion(
        source: str | Path,
        destination: str | Path,
        **kwargs: object,
    ) -> None:
        nonlocal promoted
        real_replace(source, destination, **kwargs)
        if Path(source).name.startswith(".projection.pointer-"):
            promoted = True

    def fail_durable_fsync(descriptor: int) -> None:
        nonlocal failed
        if promoted and not failed:
            failed = True
            v3_root.rename(detached_v3)
            v3_root.symlink_to(outside, target_is_directory=True)
            (outside / "projection").symlink_to("outside-old")
            raise OSError("parent fsync failed")
        real_fsync(descriptor)

    monkeypatch.setattr(projection_module.os, "replace", observe_promotion)
    monkeypatch.setattr(projection_module.os, "fsync", fail_durable_fsync)

    with pytest.raises(OSError, match="parent fsync failed"):
        writer.project(_snapshot(snapshot_id="snapshot:kis:2", text="changed"))

    assert failed is True
    assert os.readlink(outside / "projection") == "outside-old"
    v3_root.unlink()
    detached_v3.rename(v3_root)
    assert os.readlink(writer.projection_root) == old_target


def test_missing_dir_fd_capability_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tradecraft.services import jue_wiki_projection as projection_module

    writer = JueWikiProjectionWriter(tmp_path / "projection")
    monkeypatch.delattr(projection_module.os, "O_NOFOLLOW")

    with pytest.raises(
        WikiProjectionError,
        match="projection_dir_fd_unsupported",
    ):
        writer.project(_snapshot())

    assert not writer.projection_root.exists()


def test_sqlite_staging_never_uses_global_tempfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("outside")
    called = False

    def substitute_global_tempfile(*args: object, **kwargs: object) -> tuple[int, str]:
        nonlocal called
        called = True
        path = outside / "substituted.sqlite3"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        return (descriptor, str(path))

    monkeypatch.setattr(tempfile, "mkstemp", substitute_global_tempfile)
    writer = JueWikiProjectionWriter(tmp_path / "projection")

    writer.project(_snapshot())

    assert called is False
    assert tuple(outside.iterdir()) == (marker,)


def test_sqlite_install_and_staging_cleanup_are_fd_relative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    real_replace = os.replace
    observed_install = False

    def observe_index_install(
        source: str | Path,
        destination: str | Path,
        **kwargs: object,
    ) -> None:
        nonlocal observed_install
        if Path(destination).name == "wiki-search.sqlite3":
            observed_install = True
            assert Path(source).name == "wiki-search.sqlite3"
            assert isinstance(kwargs.get("src_dir_fd"), int)
            assert isinstance(kwargs.get("dst_dir_fd"), int)
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(
        "tradecraft.services.jue_wiki_projection.os.replace",
        observe_index_install,
    )

    writer.project(_snapshot())

    assert observed_install is True
    assert tuple(tmp_path.glob(".projection.staging-*")) == ()


@pytest.mark.parametrize(
    "unsupported_errno",
    sorted({errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL}),
)
def test_capability_os_errors_are_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsupported_errno: int,
) -> None:
    from tradecraft.services import jue_wiki_projection as projection_module

    real_mkdir = os.mkdir

    def fail_capability_mkdir(
        path: str | Path,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if ".capability-" in str(path):
            raise OSError(unsupported_errno, "unsupported")
        real_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(projection_module.os, "mkdir", fail_capability_mkdir)
    writer = JueWikiProjectionWriter(tmp_path / "projection")

    with pytest.raises(
        WikiProjectionError,
        match="projection_dir_fd_unsupported",
    ):
        writer.project(_snapshot())


def test_generation_open_failure_removes_orphan_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    real_open_directory = writer._open_directory_at

    def fail_generation_open(parent_fd: int, name: str) -> int:
        if name.startswith(".projection.generation-"):
            raise OSError("generation open failed")
        return real_open_directory(parent_fd, name)

    monkeypatch.setattr(writer, "_open_directory_at", fail_generation_open)

    with pytest.raises(OSError, match="generation open failed"):
        writer.project(_snapshot())

    assert tuple(tmp_path.glob(".projection.generation-*")) == ()


def test_containment_fstat_failure_closes_every_acquired_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    containment = tmp_path / "runtime"
    containment.mkdir()
    writer = JueWikiProjectionWriter(
        containment / ".v3" / "projection",
        containment_root=containment,
    )
    from tradecraft.services import jue_wiki_projection as projection_module

    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    opened: list[int] = []
    closed: list[int] = []

    def track_open(*args: object, **kwargs: object) -> int:
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def track_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    def fail_fstat(descriptor: int) -> os.stat_result:
        if descriptor in opened:
            raise OSError("fstat injected")
        return real_fstat(descriptor)

    monkeypatch.setattr(projection_module.os, "open", track_open)
    monkeypatch.setattr(projection_module.os, "close", track_close)
    monkeypatch.setattr(projection_module.os, "fstat", fail_fstat)

    with pytest.raises(
        WikiProjectionError,
        match="projection_containment_invalid",
    ):
        writer.project(_snapshot())

    assert all(closed.count(descriptor) >= opened.count(descriptor) for descriptor in opened)
