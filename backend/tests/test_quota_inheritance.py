from __future__ import annotations

import pytest
from pydantic import ValidationError

from deerflow.config.publishing_config import PublishingConfig
from deerflow.publishing.quota import (
    PlatformQuota,
    resolve_effective_quota,
)


def _platform() -> PlatformQuota:
    return PlatformQuota(
        max_concurrent_runs_per_agent=8,
        max_input_bytes=100_000,
        max_run_seconds=600,
        max_tokens_per_run=20_000,
        inbound_rps=50,
        daily_runs_default=1_000,
        daily_tokens_default=2_000_000,
    )


def test_unset_values_inherit_platform_defaults():
    quota = resolve_effective_quota(_platform(), {}, {})
    assert quota.max_concurrent_runs == 8
    assert quota.daily_runs == 1_000
    assert quota.daily_tokens == 2_000_000
    assert quota.max_run_seconds == 600
    assert quota.max_tokens_per_run == 20_000
    assert quota.max_input_bytes == 100_000
    assert quota.inbound_rps == 50


def test_publishing_config_validates_platform_quota():
    config = PublishingConfig.model_validate(
        {"platform_quota": {"daily_runs_default": 25, "inbound_rps": 3}}
    )
    assert config.platform_quota.daily_runs_default == 25
    assert config.platform_quota.inbound_rps == 3
    with pytest.raises(ValidationError):
        PublishingConfig.model_validate(
            {"platform_quota": {"daily_runs_default": 0}}
        )


def test_owner_override_can_only_tighten_platform_limit():
    quota = resolve_effective_quota(
        _platform(),
        {"max_concurrent_runs": 3, "daily_runs": 100},
        {},
    )
    assert quota.max_concurrent_runs == 3
    assert quota.daily_runs == 100

    attempted_bypass = resolve_effective_quota(
        _platform(),
        {"max_concurrent_runs": 99, "daily_runs": 99_999},
        {},
    )
    assert attempted_bypass.max_concurrent_runs == 8
    assert attempted_bypass.daily_runs == 1_000


def test_key_override_can_tighten_owner_but_never_expand_it():
    quota = resolve_effective_quota(
        _platform(),
        {"max_concurrent_runs": 4, "daily_tokens": 50_000},
        {"max_concurrent_runs": 2, "daily_tokens": 75_000},
    )
    assert quota.max_concurrent_runs == 2
    assert quota.daily_tokens == 50_000
    assert quota.max_tokens_per_run == 20_000


def test_daily_token_limit_also_caps_per_run_reservation():
    quota = resolve_effective_quota(
        _platform(),
        {"daily_tokens": 5_000},
        {},
    )
    assert quota.daily_tokens == 5_000
    assert quota.max_tokens_per_run == 5_000


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "10"])
def test_invalid_override_is_rejected(invalid):
    with pytest.raises(ValueError, match="positive integer"):
        resolve_effective_quota(_platform(), {"daily_runs": invalid}, {})
