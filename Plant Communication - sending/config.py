"""
HERA environment configuration (TEST / TEST_BIS).
DEV is intentionally omitted — its backends are unauthenticated.
"""

from __future__ import annotations

from typing import Literal

EnvName = Literal["TEST", "TEST_BIS"]

# Shared TEST API endpoints (Profile Calculation + Plant services)
_TEST_API = {
    "profile_calc_base": "https://heraprofilecalculationservicetest.azurewebsites.net",
    "plant_base": "https://heraplantservicetest.azurewebsites.net",
}

ENVIRONMENTS: dict[EnvName, dict[str, str | int]] = {
    "TEST": {
        "label": "Test",
        **_TEST_API,
        "mqtt_host": "646013b8ff8b4bf2a961ca9068b8c6d4.s1.eu.hivemq.cloud",
        "mqtt_port": 8883,
        "mqtt_user": "HERA2",
        "mqtt_pass": "9L4HhKBcWm3x0ik",
        "mqtt_ca_file": "./certs/isrgrootx1.pem",
    },
    "TEST_BIS": {
        "label": "Test (EMQX)",
        **_TEST_API,
        "mqtt_host": "o46f6915.ala.eu-central-1.emqxsl.com",
        "mqtt_port": 8883,
        "mqtt_user": "test",
        "mqtt_pass": "test",
    },
}

ENV_CHOICES: list[EnvName] = ["TEST", "TEST_BIS"]

ENV_PILL_COLORS: dict[EnvName, str] = {
    "TEST":     "#8250df",
    "TEST_BIS": "#d97706",
}

DEFAULT_ENV: EnvName = "TEST"
_current_env: EnvName = DEFAULT_ENV


def get_env() -> EnvName:
    return _current_env


def set_env(env: str) -> None:
    global _current_env
    key = env.upper().replace(" ", "_")
    if key not in ENVIRONMENTS:
        raise ValueError(
            f"Unknown environment {env!r}; expected one of {', '.join(ENV_CHOICES)}"
        )
    _current_env = key  # type: ignore[assignment]


def get_config() -> dict[str, str | int]:
    return ENVIRONMENTS[_current_env]
