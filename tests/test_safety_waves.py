from __future__ import annotations

from vireonet.safety import run_waves


async def test_waves_run_sequentially_and_complete() -> None:
    seen: list[list[str]] = []

    async def ok(wave: list[str]) -> list[str]:
        seen.append(list(wave))
        return []

    r = await run_waves(["a", "b", "c"], ok, wave_size=2, abort_threshold=1)
    assert (
        seen == [["a", "b"], ["c"]] and not r.aborted and r.completed_waves == [["a", "b"], ["c"]]
    )


async def test_abort_threshold_stops_remaining_waves() -> None:
    seen: list[list[str]] = []

    async def fail_first(wave: list[str]) -> list[str]:
        seen.append(list(wave))
        return list(wave) if wave == ["a", "b"] else []

    r = await run_waves(["a", "b", "c", "d"], fail_first, wave_size=2, abort_threshold=1)
    assert r.aborted and seen == [["a", "b"]] and r.failed_devices == ["a", "b"]


async def test_abort_threshold_two_tolerates_one_failure() -> None:
    async def fail_one(wave: list[str]) -> list[str]:
        return ["a"] if "a" in wave else []

    r = await run_waves(["a", "b", "c"], fail_one, wave_size=2, abort_threshold=2)
    assert not r.aborted  # 1 failure < threshold 2, rollout continues
