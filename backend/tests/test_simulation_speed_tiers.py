"""仿真倍速：后端仅允许 0.5 / 1 / 2 三档（请求校验 + 导入归整）。"""

import pytest
from pydantic import ValidationError

from app.schemas.simulation import (
    SetSpeedRequest,
    normalize_simulation_speed_multiplier,
    parse_strict_speed_multiplier,
)


def test_normalize_snaps_to_nearest_tier():
    assert normalize_simulation_speed_multiplier(4.0) == 2.0
    assert normalize_simulation_speed_multiplier(0.25) == 0.5
    assert normalize_simulation_speed_multiplier(1.4) == 1.0


def test_strict_accepts_only_three_values():
    assert parse_strict_speed_multiplier(1.0) == 1.0
    assert parse_strict_speed_multiplier(0.5) == 0.5
    assert parse_strict_speed_multiplier(2.0) == 2.0
    with pytest.raises(ValueError):
        parse_strict_speed_multiplier(1.5)


def test_set_speed_request_validation():
    SetSpeedRequest(speed_multiplier=2.0)
    with pytest.raises(ValidationError):
        SetSpeedRequest(speed_multiplier=3.0)
