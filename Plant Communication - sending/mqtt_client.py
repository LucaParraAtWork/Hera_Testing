"""
Background MQTT client for HOF plant communication dashboard.
Runs in a daemon thread; stores messages in a thread-safe deque so
Streamlit can consume them without blocking the UI.
"""

import json
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

import paho.mqtt.client as mqtt

from config import get_config

# ── Broker configuration (resolved per active environment) ───────────────────
SUBSCRIBE_TOPIC = "HOF/+/+/CMD/#"

# Keep at most this many raw messages in memory
MAX_MESSAGES = 500

# Device-category keywords to classify the second topic level
DEVICE_ALIASES: Dict[str, str] = {
    "ELECTROLYSER":  "Electrolyser",
    "ELECTRO":       "Electrolyser",
    "ELY":           "Electrolyser",
    "RECTIFIER":     "Rectifier",
    "RECT":          "Rectifier",
    "LOADING":       "Loading Bay",
    "LOADINGBAY":    "Loading Bay",
    "LOADING_BAY":   "Loading Bay",
    "LB":            "Loading Bay",
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class MqttMessage:
    received_at: datetime
    topic: str
    device_category: str
    device_id: str
    cmd_suffix: str
    raw_payload: str
    payload: Any          # parsed JSON, or raw string

    @property
    def topic_parts(self) -> List[str]:
        return self.topic.split("/")


def _classify_device(level2: str) -> str:
    """Map raw topic level-2 token to a friendly device category."""
    key = level2.upper().replace("-", "_").replace(" ", "_")
    return DEVICE_ALIASES.get(key, level2.upper())


def _parse_payload(raw: bytes) -> tuple[str, Any]:
    """Return (raw_string, parsed_value). Falls back gracefully."""
    raw_str = raw.decode("utf-8", errors="replace").strip()
    try:
        return raw_str, json.loads(raw_str)
    except (json.JSONDecodeError, ValueError):
        try:
            return raw_str, float(raw_str)
        except ValueError:
            return raw_str, raw_str


# ── Shared state ─────────────────────────────────────────────────────────────

class BrokerState:
    """Thread-safe container shared between the MQTT thread and Streamlit."""

    def __init__(self):
        self._lock = threading.Lock()
        self.messages: Deque[MqttMessage] = deque(maxlen=MAX_MESSAGES)
        self.connected: bool = False
        self.connect_error: Optional[str] = None
        self.stats: Dict[str, int] = {}   # category → message count

    # -- write side (called from MQTT thread) ----------------------------------

    def add_message(self, msg: MqttMessage):
        with self._lock:
            self.messages.append(msg)
            self.stats[msg.device_category] = (
                self.stats.get(msg.device_category, 0) + 1
            )

    def set_connected(self, value: bool, error: Optional[str] = None):
        with self._lock:
            self.connected = value
            self.connect_error = error

    # -- read side (called from Streamlit main thread) ------------------------

    def snapshot(self) -> List[MqttMessage]:
        with self._lock:
            return list(self.messages)

    def stats_snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self.stats)

    def is_connected(self) -> tuple[bool, Optional[str]]:
        with self._lock:
            return self.connected, self.connect_error


# ── Singleton broker state ─────────────────────────────────────────────────

_state: Optional[BrokerState] = None
_client: Optional[mqtt.Client] = None
_thread: Optional[threading.Thread] = None


def get_state() -> BrokerState:
    global _state
    if _state is None:
        _state = BrokerState()
    return _state


# ── MQTT callbacks ────────────────────────────────────────────────────────────

def _on_connect(client, userdata, flags, reason_code, properties=None):
    state: BrokerState = userdata
    if reason_code == 0 or reason_code == mqtt.MQTT_ERR_SUCCESS:
        state.set_connected(True)
        client.subscribe(SUBSCRIBE_TOPIC, qos=1)
    else:
        state.set_connected(False, error=f"RC={reason_code}")


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    state: BrokerState = userdata
    state.set_connected(False, error=f"Disconnected (RC={reason_code})")


def _on_message(client, userdata, raw_msg):
    state: BrokerState = userdata
    parts = raw_msg.topic.split("/")

    # Expected pattern: HOF / <device_type> / <device_id> / CMD / [suffix…]
    device_category = _classify_device(parts[1]) if len(parts) > 1 else "UNKNOWN"
    device_id       = parts[2] if len(parts) > 2 else "?"
    cmd_suffix      = "/".join(parts[4:]) if len(parts) > 4 else (parts[3] if len(parts) > 3 else "")

    raw_str, parsed = _parse_payload(raw_msg.payload)

    msg = MqttMessage(
        received_at=datetime.now(tz=timezone.utc),
        topic=raw_msg.topic,
        device_category=device_category,
        device_id=device_id,
        cmd_suffix=cmd_suffix,
        raw_payload=raw_str,
        payload=parsed,
    )
    state.add_message(msg)


# ── Client lifecycle ──────────────────────────────────────────────────────────

def _build_client(state: BrokerState) -> mqtt.Client:
    cfg = get_config()
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"hof-dashboard-{int(time.time())}",
        protocol=mqtt.MQTTv5,
    )
    client.username_pw_set(str(cfg["mqtt_user"]), str(cfg["mqtt_pass"]))

    ca_file = cfg.get("mqtt_ca_file")
    if ca_file:
        tls_ctx = ssl.create_default_context(cafile=str(ca_file))
    else:
        tls_ctx = ssl.create_default_context()
    client.tls_set_context(tls_ctx)

    client.on_connect    = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message    = _on_message
    client.user_data_set(state)
    return client


def _run_loop(state: BrokerState):
    """Blocking network loop run in a daemon thread with auto-reconnect."""
    global _client
    while True:
        cfg = get_config()
        try:
            _client = _build_client(state)
            _client.connect(
                str(cfg["mqtt_host"]),
                int(cfg["mqtt_port"]),
                keepalive=60,
            )
            _client.loop_forever()
        except Exception as exc:
            state.set_connected(False, error=str(exc))
            time.sleep(5)  # back-off before reconnect


def stop_mqtt() -> None:
    """Disconnect the active client; the background thread reconnects on its own."""
    global _client
    if _client is not None:
        try:
            _client.disconnect()
        except Exception:
            pass


def reconnect_mqtt(state: Optional[BrokerState] = None) -> BrokerState:
    """Apply the current environment MQTT settings (disconnect → auto-reconnect)."""
    if state is None:
        state = get_state()
    stop_mqtt()
    if _thread is None or not _thread.is_alive():
        return start_mqtt(state, force=True)
    return state


def start_mqtt(state: Optional[BrokerState] = None, *, force: bool = False) -> BrokerState:
    """
    Start the MQTT background thread (idempotent – safe to call on every
    Streamlit rerun because it checks if the thread is already alive).
    Pass force=True after stop_mqtt() to spawn a new thread.
    """
    global _thread
    if state is None:
        state = get_state()

    if not force and _thread is not None and _thread.is_alive():
        return state  # already running

    _thread = threading.Thread(
        target=_run_loop,
        args=(state,),
        daemon=True,
        name="mqtt-loop",
    )
    _thread.start()
    return state
