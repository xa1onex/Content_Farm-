from unittest.mock import Mock
import pytest
from shortpost_worker import power


def test_unattended_run_prevents_idle_sleep_and_restores_previous_state(monkeypatch):
    setter=Mock(return_value=power.CONTINUOUS);monkeypatch.setattr(power,'windows_setter',lambda:setter)
    with power.prevent_idle_sleep():setter.assert_called_once_with(power.CONTINUOUS|power.SYSTEM_REQUIRED)
    assert setter.call_args.args==(power.CONTINUOUS,)


def test_cleanup_preserves_existing_requirements_even_on_error(monkeypatch):
    setter=Mock(return_value=power.CONTINUOUS|2);monkeypatch.setattr(power,'windows_setter',lambda:setter)
    with pytest.raises(ValueError):
        with power.prevent_idle_sleep():raise ValueError('stop')
    assert setter.call_args.args==(power.CONTINUOUS|2,)


def test_diagnostics_do_not_hold_system_awake(monkeypatch):
    getter=Mock();monkeypatch.setattr(power,'windows_setter',getter)
    with power.prevent_idle_sleep(enabled=False):pass
    getter.assert_not_called()


def test_failed_request_is_logged_and_does_not_reset_other_state(monkeypatch,caplog):
    setter=Mock(return_value=0);monkeypatch.setattr(power,'windows_setter',lambda:setter)
    with power.prevent_idle_sleep():pass
    assert setter.call_count==1 and 'idle_sleep_prevention_unavailable' in caplog.text
