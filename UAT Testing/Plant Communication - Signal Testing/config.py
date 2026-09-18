"""
Broker configuration (DEV / TEST / TEST_BIS).

Standalone copy of the same environments already used by
`../Plant Communication - sending/config.py` (which itself mirrors
hera-virtual-plant's own config.py). Kept as a separate copy on purpose: this
folder must work even after hera-virtual-plant stops existing (see README.md)
-- it has no import dependency on that repo or on the "sending" folder.
"""

from __future__ import annotations

from typing import Literal

EnvName = Literal["DEV", "TEST", "TEST_BIS"]

# Shared TEST API endpoints (Profile Calculation + Plant services) -- not used
# by this folder's scripts (MQTT-only), kept for parity/reference with the
# sibling config.py files.
_TEST_API = {
    "profile_calc_base": "https://heraprofilecalculationservicetest.azurewebsites.net",
    "plant_base": "https://heraplantservicetest.azurewebsites.net",
}

ENVIRONMENTS: dict[EnvName, dict[str, str | int]] = {
    "DEV": {
        "label": "Development",
        "mqtt_host": "a6af9b771c814f19adf12e1b3a7cbb75.s1.eu.hivemq.cloud",
        "mqtt_port": 8883,
        "mqtt_user": "HERA3",
        "mqtt_pass": "HeraTestUserThing01",
    },
    "TEST": {
        "label": "Test",
        **_TEST_API,
        "mqtt_host": "646013b8ff8b4bf2a961ca9068b8c6d4.s1.eu.hivemq.cloud",
        "mqtt_port": 8883,
        "mqtt_user": "HERA2",
        "mqtt_pass": "9L4HhKBcWm3x0ik",
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

ENV_CHOICES: list[EnvName] = ["DEV", "TEST", "TEST_BIS"]

DEFAULT_ENV: EnvName = "TEST"


def get_config(env: str | None = None) -> dict[str, str | int]:
    """Return the broker config for *env* (default: DEFAULT_ENV)."""
    key = (env or DEFAULT_ENV).strip().upper().replace(" ", "_").replace("-", "_")
    if key not in ENVIRONMENTS:
        raise ValueError(f"Unknown environment {env!r}; expected one of {', '.join(ENV_CHOICES)}")
    return ENVIRONMENTS[key]  # type: ignore[return-value]
