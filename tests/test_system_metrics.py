from __future__ import annotations

from dataclasses import dataclass

from tradecraft.services.system_metrics import SystemMetricsService


@dataclass
class _Mem:
    total: int
    available: int
    used: int
    percent: float


@dataclass
class _Swap:
    total: int
    used: int
    free: int
    percent: float


@dataclass
class _Net:
    bytes_sent: int
    bytes_recv: int


@dataclass
class _Disk:
    total: int
    used: int
    free: int
    percent: float


@dataclass
class _Info:
    rss: int


class _Proc:
    def __init__(
        self,
        *,
        pid: int,
        name: str,
        cmdline: list[str],
        rss: int,
        cpu: float,
    ) -> None:
        self.info = {
            "pid": pid,
            "name": name,
            "cmdline": cmdline,
            "memory_info": _Info(rss=rss),
            "create_time": 1_779_000_000.0,
            "status": "running",
        }
        self._cpu = cpu

    def cpu_percent(self, interval=None) -> float:  # noqa: ANN001
        return self._cpu


class _Psutil:
    NoSuchProcess = RuntimeError
    AccessDenied = RuntimeError
    ZombieProcess = RuntimeError

    def __init__(self) -> None:
        self.cpu_calls = 0
        self.net_values = [
            _Net(bytes_sent=1_000, bytes_recv=2_000),
            _Net(bytes_sent=2_500, bytes_recv=5_000),
        ]
        self.processes = [
            _Proc(
                pid=101,
                name="python",
                cmdline=["python", ".venv/bin/tradecraft-binance-block-trader"],
                rss=120_000_000,
                cpu=4.2,
            ),
            _Proc(
                pid=202,
                name="python",
                cmdline=["python", ".venv/bin/tradecraft-control"],
                rss=80_000_000,
                cpu=2.0,
            ),
            _Proc(
                pid=203,
                name="python",
                cmdline=["python", ".venv/bin/tradecraft-live-evaluator"],
                rss=30_000_000,
                cpu=1.0,
            ),
            _Proc(
                pid=206,
                name="python",
                cmdline=["python", ".venv/bin/tradecraft-watchdog"],
                rss=20_000_000,
                cpu=0.5,
            ),
            _Proc(
                pid=207,
                name="python",
                cmdline=[
                    "python",
                    "-c",
                    "from tradecraft.runtime.jue_wiki_runner import run; run()",
                ],
                rss=10_000_000,
                cpu=0.3,
            ),
            _Proc(
                pid=208,
                name="tee",
                cmdline=["tee", "-a", ".runtime/jue_wiki_runner.log"],
                rss=5_000_000,
                cpu=0.2,
            ),
            _Proc(
                pid=204,
                name="zsh",
                cmdline=[
                    "zsh",
                    "-c",
                    "cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-control",
                ],
                rss=40_000_000,
                cpu=7.0,
            ),
            _Proc(
                pid=205,
                name="tmux",
                cmdline=[
                    "tmux",
                    "new-session",
                    ".venv/bin/tradecraft-binance-block-trader",
                ],
                rss=30_000_000,
                cpu=6.0,
            ),
            _Proc(
                pid=303,
                name="Other",
                cmdline=["/usr/bin/Other"],
                rss=500_000_000,
                cpu=90.0,
            ),
        ]

    def cpu_percent(self, interval=None) -> float:  # noqa: ANN001
        self.cpu_calls += 1
        return 12.5

    def virtual_memory(self) -> _Mem:
        return _Mem(total=16_000, available=10_000, used=6_000, percent=37.5)

    def swap_memory(self) -> _Swap:
        return _Swap(total=4_000, used=1_000, free=3_000, percent=25.0)

    def disk_usage(self, path: str) -> _Disk:
        assert path
        return _Disk(total=100_000, used=25_000, free=75_000, percent=25.0)

    def net_io_counters(self) -> _Net:
        return self.net_values[min(self.cpu_calls - 1, len(self.net_values) - 1)]

    def process_iter(self, attrs):  # noqa: ANN001
        assert "cmdline" in attrs
        return list(self.processes)


def test_system_metrics_snapshot_is_cached_and_lightweight() -> None:
    psutil = _Psutil()
    now = 1000.0
    service = SystemMetricsService(
        psutil_module=psutil,
        time_func=lambda: now,
        cache_ttl_sec=10.0,
    )

    first = service.snapshot()
    second = service.snapshot()

    assert first["status"] == "ok"
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert psutil.cpu_calls == 1
    assert first["system"]["cpu_percent"] == 12.5
    assert first["hermes"]["process_count"] == 5
    assert first["hermes"]["wrapper_process_count"] == 3
    assert first["hermes"]["memory_rss_bytes"] == 260_000_000
    assert first["hermes"]["cpu_percent"] == 8.0
    assert first["hermes"]["processes"][0]["component"] == "binance_block_trader"
    assert {row["component"] for row in first["hermes"]["processes"]} == {
        "binance_block_trader",
        "control",
        "jue_wiki",
        "live_evaluator",
        "watchdog",
    }


def test_system_metrics_network_rates_use_delta_after_cache_expires() -> None:
    psutil = _Psutil()
    clock = {"now": 1000.0}
    service = SystemMetricsService(
        psutil_module=psutil,
        time_func=lambda: clock["now"],
        cache_ttl_sec=10.0,
    )

    service.snapshot()
    clock["now"] += 12.0
    second = service.snapshot()

    assert second["cache"]["hit"] is False
    assert second["network"]["interval_sec"] == 12.0
    assert second["network"]["sent_per_sec"] == 125.0
    assert second["network"]["recv_per_sec"] == 250.0
