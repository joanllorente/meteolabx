from datetime import datetime
from types import SimpleNamespace

import pytest


class StreamlitRecorder:
    def __init__(self, session_state=None):
        self.session_state = session_state if session_state is not None else {}
        self.info_messages = []
        self.warning_messages = []
        self.caption_messages = []

    def info(self, message):
        self.info_messages.append(message)

    def warning(self, message):
        self.warning_messages.append(message)

    def caption(self, message):
        self.caption_messages.append(message)

    def columns(self, count):
        return tuple(SimpleNamespace() for _ in range(count))


@pytest.fixture
def fake_session_state():
    return {}


@pytest.fixture
def streamlit_recorder(fake_session_state):
    return StreamlitRecorder(session_state=fake_session_state)


@pytest.fixture
def patch_streamlit(monkeypatch, streamlit_recorder):
    def _patch(module):
        monkeypatch.setattr(module, "st", streamlit_recorder)
        return streamlit_recorder

    return _patch


@pytest.fixture
def translation_stub():
    def _t(key, **kwargs):
        if not kwargs:
            return key
        return f"{key}::{kwargs}"

    return _t


@pytest.fixture
def note_recorder():
    calls = []

    def _render(message):
        calls.append(message)

    _render.calls = calls
    return _render


@pytest.fixture
def fake_logger():
    class _Logger:
        def __init__(self):
            self.warning_messages = []

        def warning(self, message):
            self.warning_messages.append(message)

    return _Logger()


@pytest.fixture
def climograms_service_factory():
    def _factory(periods=None, description="range"):
        default_periods = periods
        if default_periods is None:
            default_periods = [
                SimpleNamespace(
                    start=datetime(2025, 1, 1),
                    end=datetime(2025, 1, 31),
                )
            ]

        class _Service:
            def build_period_specs(self, summary_mode, selected_years, selected_months):
                return list(default_periods)

            def describe_period_range(self, built_periods):
                return description

        return _Service()

    return _factory
