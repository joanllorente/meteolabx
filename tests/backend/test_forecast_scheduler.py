"""Continuous scheduling: canonical state, readiness and background refresh."""
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from scripts import forecast_scheduler as s
from scripts import forecast_worker as w
from server.services.forecast_store import LocalObjectStore, read_json, run_manifest_key

RUN = "2026-09-21T00:00:00Z"
H1 = "2026-09-21T01:00:00Z"
H2 = "2026-09-21T02:00:00Z"


def job(tier=2, hour=H1, products=None):
    return w.ForecastJob(RUN, hour, products or (("dcape",) if tier == 3 else ("ship",)), "model", tier)


def catalog(*hours):
    return {"products": {"ship": {"run": RUN, "valid_times": list(hours)}}}


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(s, "_packages_available", lambda: True)
    monkeypatch.setattr(w, "_room_for_another_profile", lambda: True)
    r = s.Readiness(w)
    r.observe({"run": RUN, "expected_times": [H1, H2]}, 0)
    monkeypatch.setattr(s.packages, "_partial_sizes", lambda path: {})
    return r


def test_refresh_preserves_active_manifest_and_late_completion(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "_record_downloads", lambda m: None)
    registry = s.Manifests(w, LocalObjectStore(tmp_path), "model", 0)
    registry.merge(catalog(H1))
    original = registry.items[RUN]
    work = job()
    w._mark_job_started(original, work, 1800)
    registry.merge(catalog(H1, H2))
    assert registry.items[RUN] is original
    assert original["progress"]["active_jobs"][0]["id"] == w._job_id(work)
    w._mark_job_finished(original, work)
    registry.persist(original)
    saved = read_json(registry.store, run_manifest_key(RUN))
    assert saved["expected_times"] == [H1, H2]
    assert saved["products"]["ship"]["available_times"] == [H1]
    assert saved["progress"]["active_jobs"] == []


def test_merge_does_not_prune_live_runs(monkeypatch, tmp_path):
    registry = s.Manifests(w, LocalObjectStore(tmp_path), "model", 0)
    monkeypatch.setattr(w, "_publish_run_slot", lambda *a: pytest.fail("unsafe publish"))
    monkeypatch.setattr(w, "_prune_old_runs", lambda *a: pytest.fail("unsafe prune"))
    registry.merge(catalog(H1))
    registry.merge({"products": {"ship": {"run": "2026-09-22T00:00:00Z", "valid_times": []}}})
    assert RUN in registry.items


def test_profile_requires_ip3_and_first_seen_does_not_reset(ready, monkeypatch):
    monkeypatch.setattr(s.packages, "package_ready", lambda p, *a: p == "IP1")
    assert ready.mode(job(), 170) == "publication"
    ready.observe({"run": RUN, "expected_times": [H1]}, 179)
    assert ready.mode(job(), 181) == "wcs"
    assert ready.mode(job(3), 500) == "publication"
    monkeypatch.setattr(s.packages, "package_ready", lambda *a: True)
    assert ready.mode(job(3), 500) == "ready"


def test_growing_download_outlives_publication_budget_but_orphan_expires(ready, monkeypatch):
    monkeypatch.setattr(s.packages, "package_ready", lambda *a: False)
    size = [1]
    monkeypatch.setattr(s.packages, "_partial_sizes", lambda p: {("partial", 1): size[0]})
    assert ready.mode(job(), 170) == "downloading"
    for now in (210, 250, 290, 330):
        size[0] += 1
        assert ready.mode(job(), now) == "downloading"
    assert ready.mode(job(), 391) == "wcs"


def test_cross_tier_ready_and_combined_heavy_capacity(ready, monkeypatch):
    monkeypatch.setattr(s.packages, "package_ready", lambda p, r, v: v.hour == 2)
    pending = [({}, job()), ({}, job(3, H2))]
    assert s.select_ready(w, pending, {}, ready, 100, 0, 4, 2) == (1, "ready")
    active = {1: ({}, job(2, "2026-09-21T03:00:00Z"), "ready"),
              2: ({}, job(3, "2026-09-21T04:00:00Z"), "ready")}
    assert s.select_ready(w, pending, active, ready, 100, 0, 4, 2) is None
    # Native work can still use the other slots.
    pending.append(({}, job(0, products=("temperature-850",))))
    assert s.select_ready(w, pending, active, ready, 100, 0, 4, 2) == (2, "ready")


def test_wcs_serialization_does_not_block_a_cached_dcape(ready, monkeypatch):
    monkeypatch.setattr(s.packages, "package_ready", lambda p, r, v: v.hour == 2)
    active = {1: ({}, job(2, "2026-09-21T03:00:00Z"), "wcs")}
    pending = [({}, job()), ({}, job(3, H2))]
    assert s.select_ready(w, pending, active, ready, 200, 180, 4, 4) == (1, "ready")
    assert s.select_ready(w, pending[:1], active, ready, 200, 180, 4, 4) is None


def test_memory_and_15_seconds_still_gate_heavy_jobs(ready, monkeypatch):
    monkeypatch.setattr(s.packages, "package_ready", lambda *a: True)
    pending = [({}, job(3, H2))]
    active = {1: ({}, job(), "ready")}
    assert s.select_ready(w, pending, active, ready, 14, 0, 4, 4) is None
    monkeypatch.setattr(w, "_room_for_another_profile", lambda: False)
    assert s.select_ready(w, pending, active, ready, 16, 0, 4, 4) is None


def test_overlapping_group_is_not_launched_after_catalog_expansion(ready):
    old = w.ForecastJob(RUN, H1, ("accumulated-precip",), "model", 1, (H1,))
    expanded = w.ForecastJob(RUN, H1, old.products, "model", 1, (H1, H2))
    assert s.select_ready(w, [({}, expanded)], {1: ({}, old, "ready")}, ready, 200, 0, 4, 4) is None


def test_scheduled_profile_does_not_repeat_publication_wait(monkeypatch, tmp_path):
    from server.services import arome_forecast as f
    monkeypatch.setenv("METEOLABX_AROME_SCHEDULED_PACKAGES", "1")
    monkeypatch.setattr(s.packages, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(s.packages, "_download_package", lambda *a: pytest.fail("must not start download"))
    with pytest.raises(f.AromePackageError):
        f._ensure_profile_package("IP1", w._parse_iso(RUN), w._parse_iso(H1))


@pytest.mark.parametrize("quota", [0, 2])
def test_watch_refresh_never_drains_deduplicates_and_stops_orderly(monkeypatch, tmp_path, quota):
    """Virtual time: catalog takes 1.5s, one job 4s, others 0.75s."""
    clock = SimpleNamespace(now=100.0)
    submitted, completions, refreshes, prefetches = [], [], [], []
    hours = [f"2026-09-21T{i:02d}:00:00Z" for i in range(1, 12)]
    store = LocalObjectStore(tmp_path)
    monkeypatch.setattr(w, "get_settings", lambda: SimpleNamespace(arome_api_key="test"))
    monkeypatch.setattr(w, "get_forecast_store", lambda: store)
    monkeypatch.setattr(w, "forecast_calculation_scope", lambda: "model")
    monkeypatch.setattr(w, "_record_downloads", lambda m: None)
    monkeypatch.setattr(w, "_sample_resources", lambda m: None)

    class Stop:
        def is_set(self): return clock.now >= 106
        def wait(self, seconds): clock.now += seconds
    monkeypatch.setattr(s, "time", SimpleNamespace(monotonic=lambda: clock.now,
                                                   sleep=lambda seconds: setattr(clock, "now", clock.now + seconds)))

    class Deferred(Future):
        def __init__(self, when, result, work=None):
            super().__init__()
            self.when, self.value, self.work = when, result, work
        def done(self):
            if clock.now >= self.when and not super().done():
                if self.work:
                    completions.append((clock.now, self.work))
                self.set_result(self.value)
            return super().done()

    class Executor:
        def __init__(self, *, max_workers, thread_name_prefix):
            self.catalog = thread_name_prefix == "arome-catalog"
            self.futures = []
        def __enter__(self): return self
        def __exit__(self, *args):
            assert all(f.done() for f in self.futures) or self.catalog
        def submit(self, function, *args, **kwargs):
            if self.catalog:
                refreshes.append(clock.now)
                value = {"products": {"temperature-850": {"run": RUN, "valid_times": hours}}}
                future = Deferred(clock.now + (1.5 if len(refreshes) > 1 else 0), value)
            else:
                work = args[0]
                assert kwargs == {"scheduled": True}
                submitted.append((clock.now, work))
                future = Deferred(clock.now + (4 if work.valid_time == H1 else .75), None, work)
            self.futures.append(future)
            return future
    monkeypatch.setattr(s, "ThreadPoolExecutor", Executor)
    def prefetch(jobs, stop):
        prefetches.append(stop)
        return SimpleNamespace(is_alive=lambda: True, join=lambda **kw: None)
    monkeypatch.setattr(w, "_start_package_prefetch", prefetch)
    args = SimpleNamespace(diagnostic_max_hours=0, max_hours=0, workers=2, interval=30,
        cycle_budget=1, max_tasks=quota, heavy_workers=2, native_timeout=300,
        derived_timeout=1800, ecmwf_max_frames=-1)
    s.run_watch(w, args, Stop())
    assert len(refreshes) >= 2
    assert len(prefetches) == 1  # Not tied to catalog cycles.
    assert prefetches[0].is_set()
    assert len({w._job_id(j) for _, j in submitted}) == len(submitted)
    assert len(completions) == len(submitted)  # SIGTERM drains live work only.
    assert all(t < 106 for t, _ in submitted)
    saved = read_json(store, run_manifest_key(RUN))
    assert len(saved["products"]["temperature-850"]["available_times"]) == len(submitted)
    assert saved["progress"]["active_jobs"] == []
    if quota == 0:
        assert any(refreshes[1] < t < refreshes[1] + 1.5 for t, _ in submitted)
    else:
        assert len(submitted) > quota  # Refresh renews quota; does not exit watch.
    # A renewal has happened while the first job was still running.
    assert refreshes[1] + 1.5 < submitted[0][0] + 4


@pytest.mark.parametrize("max_tasks,budget,expected", [(1, 0, 1), (0, 1, 2)])
def test_one_shot_keeps_task_and_budget_exit_contract(monkeypatch, tmp_path, max_tasks, budget, expected):
    clock = SimpleNamespace(now=100.0)
    store = LocalObjectStore(tmp_path)
    registry = s.Manifests(w, store, "model", 0)
    registry.merge({"products": {"temperature-850": {"run": RUN, "valid_times": [H1, H2, "2026-09-21T03:00:00Z"]}}})
    manifest = registry.items[RUN]
    jobs = w._jobs_for_manifest(manifest)
    monkeypatch.setattr(w, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=lambda n: None))
    monkeypatch.setattr(w, "_start_package_prefetch", lambda *a: None)
    monkeypatch.setattr(w, "_record_downloads", lambda m: None)
    monkeypatch.setattr(w, "_sample_resources", lambda m: None)
    def calculate(*args): clock.now += 2
    monkeypatch.setattr(w, "_run_isolated_job", calculate)
    class Executor:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, function, *args):
            function(*args)
            future = Future()
            future.set_result(None)
            return future
    monkeypatch.setattr(w, "ThreadPoolExecutor", Executor)
    completed, frames, failures = w._run_parallel_work(store=store, manifests=[manifest],
        queues={RUN: jobs}, latest_run=RUN, workers=2, heavy_workers=2,
        max_tasks=max_tasks, cycle_budget_s=budget, native_timeout_s=300, derived_timeout_s=1800)
    assert (completed, frames, failures) == (expected, expected, 0)
    assert not manifest["progress"]["active_jobs"]


def test_retired_slot_is_public_before_old_run_is_safe_to_delete(monkeypatch, tmp_path):
    registry = s.Manifests(w, LocalObjectStore(tmp_path), "model", 0)
    monkeypatch.setattr(w, "_emit_run_report", lambda *a: True)
    monkeypatch.setattr(w, "_prune_old_runs", lambda *a: None)
    registry.merge(catalog(H1))
    old = registry.items[RUN]
    registry.merge({"products": {"ship": {"run": "2026-09-22T00:00:00Z", "valid_times": []}}})
    assert registry.retired[RUN] is old
    assert read_json(registry.store, run_manifest_key(RUN)) is not None
    slots = w.retained_manifests(registry.store)
    assert [m["run"] for m in slots] == ["2026-09-22T00:00:00Z"]
    registry.retain_when_idle()
    assert RUN not in registry.items
    assert read_json(registry.store, run_manifest_key(RUN)) is None
