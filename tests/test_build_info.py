from types import SimpleNamespace

from utils import build_info


def test_railway_commit_is_used_as_short_build(monkeypatch):
    monkeypatch.setenv(
        "RAILWAY_GIT_COMMIT_SHA",
        "ABCDEF0123456789ABCDEF0123456789ABCDEF01",
    )
    build_info.app_build_id.cache_clear()

    assert build_info.app_build_id() == "abcdef0"

    build_info.app_build_id.cache_clear()


def test_local_git_commit_is_the_fallback(monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.setattr(
        build_info.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="938822a\n"),
    )
    build_info.app_build_id.cache_clear()

    assert build_info.app_build_id() == "938822a"

    build_info.app_build_id.cache_clear()


def test_invalid_or_unavailable_build_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "not-a-commit")

    def fail(*args, **kwargs):
        raise OSError("git unavailable")

    monkeypatch.setattr(build_info.subprocess, "run", fail)
    build_info.app_build_id.cache_clear()

    assert build_info.app_build_id() == "local"

    build_info.app_build_id.cache_clear()
