"""
Thin MQTT publish wrapper -- this script IS the plant, as far as the broker
is concerned. No subscribe side; publish-only, since the whole point is to
feed signals to Hera and (separately, later) check Hera's own reaction via
its API/DB, not to listen back on the broker ourselves.
"""
from __future__ import annotations

import ssl
import time
from typing import Optional

import certifi
import paho.mqtt.client as mqtt

from config import get_config


class PlantBrokerClient:
    """Connects once, publishes many messages, disconnects. Use as a
    context manager: `with PlantBrokerClient(env="TEST") as plant: ...`"""

    def __init__(self, env: Optional[str] = None, client_id: Optional[str] = None) -> None:
        self.cfg = get_config(env)
        self._client_id = client_id or f"fake-plant-{int(time.time())}"
        self._client: Optional[mqtt.Client] = None
        self._connected = False
        self._last_error = ""

    # -- lifecycle -----------------------------------------------------------

    def connect(self, timeout_s: float = 10.0) -> bool:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            protocol=mqtt.MQTTv5,
        )
        client.username_pw_set(str(self.cfg["mqtt_user"]), str(self.cfg["mqtt_pass"]))
        client.tls_set(ca_certs=certifi.where(), tls_version=ssl.PROTOCOL_TLS_CLIENT)

        def _on_connect(c, u, f, rc, props=None):
            self._connected = rc == 0 or rc == mqtt.MQTT_ERR_SUCCESS
            if not self._connected:
                self._last_error = f"rc={rc}"

        client.on_connect = _on_connect
        client.connect(str(self.cfg["mqtt_host"]), int(self.cfg["mqtt_port"]), keepalive=30)
        client.loop_start()
        self._client = client

        deadline = time.time() + timeout_s
        while time.time() < deadline and not self._connected:
            time.sleep(0.1)
        if not self._connected and not self._last_error:
            self._last_error = "connect timeout"
        return self._connected

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self._connected = False

    def __enter__(self) -> "PlantBrokerClient":
        if not self.connect():
            raise ConnectionError(
                f"Could not connect to {self.cfg['mqtt_host']}:{self.cfg['mqtt_port']} "
                f"as {self.cfg['mqtt_user']!r}: {self._last_error}"
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    # -- publish ---------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        return self._last_error

    def publish(self, topic: str, payload: str, *, retain: bool = False, qos: int = 1) -> bool:
        """Publish one message. Returns True once handed to paho (does not
        block for broker PUBACK -- good enough for a test-signal sender)."""
        if not self._client or not self._connected:
            self._last_error = "not connected"
            return False
        info = self._client.publish(topic, payload.encode("utf-8"), qos=qos, retain=retain)
        return info.rc == mqtt.MQTT_ERR_SUCCESS
