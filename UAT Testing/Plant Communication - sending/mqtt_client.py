"""
Background MQTT client for HOF plant communication dashboard.
Runs in a daemon thread; stores messages in a thread-safe deque so
Streamlit can consume them without blocking the UI.

Also:
  • emits dummy-plant heartbeats (ACS + PLC) every 10 s toward HERA
  • emits OKS blending status (oks_stat_blending_line) on the same cadence
  • listens for HERA (STL) heartbeats and echoes them immediately
"""

import json
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

import paho.mqtt.client as mqtt

from config import get_config

# ── Broker configuration (resolved per active environment) ───────────────────
SUBSCRIBE_TOPIC = "HOF/+/+/CMD/#"

CONTRACT_VERSION = "0.8.3"
HB_INTERVAL_S = 10.0
HB_STALE_S = 30.0

TOPIC_HERA_HB = f"HOF/STL/STL/TLM/heartbeat/{CONTRACT_VERSION}"
TOPIC_ACS_HB = f"HOF/ACS/ACS/TLM/heartbeat/{CONTRACT_VERSION}"
TOPIC_PLC_HB = f"HOF/PLC/PLC/TLM/heartbeat/{CONTRACT_VERSION}"
TOPIC_ACS_HERA_ECHO = f"HOF/ACS/ACS/TLM/hera_hb_echo/{CONTRACT_VERSION}"
TOPIC_PLC_HERA_ECHO = f"HOF/PLC/PLC/TLM/hera_hb_echo/{CONTRACT_VERSION}"
TOPIC_BLENDING_STATUS = f"HOF/PLC/OKS/TLM/oks_stat_blending_line/{CONTRACT_VERSION}"

# Dummy plant: blending line available (matches HERA capture / Plant.Mock default)
BLENDING_STATUS_VALUE = True

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


@dataclass
class HeartbeatInfo:
    """Last-seen timestamps for plant↔HERA heartbeats (UTC)."""
    plant_emitted_at: Optional[datetime] = None
    plant_value: Optional[str] = None
    hera_received_at: Optional[datetime] = None
    hera_value: Optional[str] = None
    hera_publisher_ts: Optional[str] = None  # envelope `ts` from STL


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


def _iso_now() -> str:
    """Publisher wall-clock stamp — ISO-8601 UTC with +00:00 (HEARTBEAT.md)."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _hb_envelope(value: str) -> bytes:
    return json.dumps(
        {
            "ts": _iso_now(),
            "value": str(value),
            "type": "str",
            "unit": "none",
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _blending_status_envelope(value: bool = BLENDING_STATUS_VALUE) -> bytes:
    """Wire format for oks_stat_blending_line (HERA capture example)."""
    return json.dumps(
        {
            "ts": _iso_now(),
            "value": bool(value),
            "type": "str",
            "unit": "none",
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _extract_hb_value(parsed: Any) -> Optional[str]:
    if isinstance(parsed, dict) and "value" in parsed:
        return str(parsed["value"])
    if parsed is None:
        return None
    return str(parsed)


# ── Shared state ─────────────────────────────────────────────────────────────

class BrokerState:
    """Thread-safe container shared between the MQTT thread and Streamlit."""

    def __init__(self):
        self._lock = threading.Lock()
        self.messages: Deque[MqttMessage] = deque(maxlen=MAX_MESSAGES)
        self.connected: bool = False
        self.connect_error: Optional[str] = None
        self.stats: Dict[str, int] = {}   # category → message count
        self.heartbeat = HeartbeatInfo()
        self._acs_hb_count = 0
        self._plc_hb_count = 0
        self._last_hera_hb_seen: Optional[str] = None

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

    def record_plant_heartbeat(self, value: str, when: Optional[datetime] = None):
        with self._lock:
            self.heartbeat.plant_emitted_at = when or datetime.now(tz=timezone.utc)
            self.heartbeat.plant_value = value

    def record_hera_heartbeat(
        self,
        value: str,
        *,
        publisher_ts: Optional[str] = None,
        when: Optional[datetime] = None,
    ):
        with self._lock:
            self.heartbeat.hera_received_at = when or datetime.now(tz=timezone.utc)
            self.heartbeat.hera_value = value
            self.heartbeat.hera_publisher_ts = publisher_ts

    def next_plant_hb_counts(self) -> tuple[str, str]:
        with self._lock:
            self._acs_hb_count = (self._acs_hb_count + 1) % 1_000_000_000
            self._plc_hb_count = (self._plc_hb_count + 1) % 1_000_000_000
            return str(self._acs_hb_count), str(self._plc_hb_count)

    def should_echo_hera(self, value: str) -> bool:
        """True when this STL heartbeat value is new (avoid echo spam on retain/dup)."""
        with self._lock:
            if value == self._last_hera_hb_seen:
                return False
            self._last_hera_hb_seen = value
            return True

    def reset_heartbeat(self):
        with self._lock:
            self.heartbeat = HeartbeatInfo()
            self._last_hera_hb_seen = None

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

    def heartbeat_snapshot(self) -> HeartbeatInfo:
        with self._lock:
            return HeartbeatInfo(
                plant_emitted_at=self.heartbeat.plant_emitted_at,
                plant_value=self.heartbeat.plant_value,
                hera_received_at=self.heartbeat.hera_received_at,
                hera_value=self.heartbeat.hera_value,
                hera_publisher_ts=self.heartbeat.hera_publisher_ts,
            )


# ── Singleton broker state ─────────────────────────────────────────────────

_state: Optional[BrokerState] = None
_client: Optional[mqtt.Client] = None
_thread: Optional[threading.Thread] = None
_hb_thread: Optional[threading.Thread] = None


def get_state() -> BrokerState:
    global _state
    if _state is None:
        _state = BrokerState()
    return _state


# ── Publish helpers ───────────────────────────────────────────────────────────

def _publish_bytes(client: mqtt.Client, topic: str, payload: bytes) -> None:
    client.publish(topic, payload, qos=1, retain=False)


def _emit_plant_heartbeats(client: mqtt.Client, state: BrokerState) -> None:
    """Publish ACS + PLC heartbeats (dummy plant → HERA)."""
    acs_val, plc_val = state.next_plant_hb_counts()
    _publish_bytes(client, TOPIC_ACS_HB, _hb_envelope(acs_val))
    _publish_bytes(client, TOPIC_PLC_HB, _hb_envelope(plc_val))
    state.record_plant_heartbeat(f"ACS={acs_val}/PLC={plc_val}")


def _emit_blending_status(client: mqtt.Client, value: bool = BLENDING_STATUS_VALUE) -> None:
    """Publish OKS blending-line status toward HERA."""
    _publish_bytes(client, TOPIC_BLENDING_STATUS, _blending_status_envelope(value))


def _echo_hera_heartbeat(client: mqtt.Client, value: str) -> None:
    """Immediately echo STL heartbeat on ACS + PLC (contract §4.1)."""
    payload = _hb_envelope(value)
    _publish_bytes(client, TOPIC_ACS_HERA_ECHO, payload)
    _publish_bytes(client, TOPIC_PLC_HERA_ECHO, payload)


# ── MQTT callbacks ────────────────────────────────────────────────────────────

def _on_connect(client, userdata, flags, reason_code, properties=None):
    state: BrokerState = userdata
    if reason_code == 0 or reason_code == mqtt.MQTT_ERR_SUCCESS:
        state.set_connected(True)
        client.subscribe(SUBSCRIBE_TOPIC, qos=1)
        client.subscribe(TOPIC_HERA_HB, qos=1)
    else:
        state.set_connected(False, error=f"RC={reason_code}")


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    state: BrokerState = userdata
    state.set_connected(False, error=f"Disconnected (RC={reason_code})")


def _on_message(client, userdata, raw_msg):
    state: BrokerState = userdata
    topic = raw_msg.topic
    parts = topic.split("/")

    raw_str, parsed = _parse_payload(raw_msg.payload)

    # ── HERA (STL) heartbeat ──────────────────────────────────────────────────
    if topic == TOPIC_HERA_HB or (
        len(parts) >= 5
        and parts[1].upper() == "STL"
        and parts[3].upper() == "TLM"
        and parts[4].lower() == "heartbeat"
    ):
        value = _extract_hb_value(parsed) or ""
        publisher_ts = parsed.get("ts") if isinstance(parsed, dict) else None
        state.record_hera_heartbeat(value, publisher_ts=publisher_ts)
        if value and state.should_echo_hera(value):
            try:
                _echo_hera_heartbeat(client, value)
            except Exception:
                pass
        return  # do not pollute CMD message log

    # Expected pattern: HOF / <device_type> / <device_id> / CMD / [suffix…]
    device_category = _classify_device(parts[1]) if len(parts) > 1 else "UNKNOWN"
    device_id       = parts[2] if len(parts) > 2 else "?"
    cmd_suffix      = "/".join(parts[4:]) if len(parts) > 4 else (parts[3] if len(parts) > 3 else "")

    msg = MqttMessage(
        received_at=datetime.now(tz=timezone.utc),
        topic=topic,
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


def _heartbeat_loop(state: BrokerState):
    """Periodically emit dummy-plant ACS/PLC heartbeats while connected."""
    last_emit = 0.0
    while True:
        time.sleep(0.5)
        client = _client
        connected, _ = state.is_connected()
        if client is None or not connected:
            continue
        now = time.monotonic()
        if last_emit != 0.0 and (now - last_emit) < HB_INTERVAL_S:
            continue
        try:
            _emit_plant_heartbeats(client, state)
            _emit_blending_status(client)
            last_emit = now
        except Exception:
            pass


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
    state.reset_heartbeat()
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
    global _thread, _hb_thread
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

    if _hb_thread is None or not _hb_thread.is_alive():
        _hb_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(state,),
            daemon=True,
            name="mqtt-heartbeat",
        )
        _hb_thread.start()

    return state
