# HOF Plant Communication Dashboard

Real-time Streamlit dashboard that subscribes to the HiveMQ cloud broker and visualises
command profiles for **Electrolyser**, **Rectifier**, and **Loading Bay**.

## Quick start

```bash
# 1 – Create a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Mac / Linux

# 2 – Install dependencies
pip install -r requirements.txt

# 3 – Run the dashboard
streamlit run app.py
```

The browser will open automatically at http://localhost:8501.

## File layout

```
├── app.py            ← Streamlit dashboard (main entry-point)
├── mqtt_client.py    ← Background MQTT thread + shared broker state
├── requirements.txt
├── certs/
│   └── isrgrootx1.pem  ← ISRG Root X1 CA cert (TLS for HiveMQ Cloud)
└── README.md
```

## Broker configuration

Broker host/credentials are selected per environment in `config.py`
(TEST / TEST_BIS). Default topic: `HOF/+/+/CMD/#`.

## Expected topic structure

```
HOF / <device_type> / <device_id> / CMD / <cmd_suffix>
 └─ e.g. HOF/ELECTROLYSER/ELY-01/CMD/PROFILE
```

The dashboard classifies incoming messages by the second topic level and
auto-detects the numeric payload regardless of the JSON shape:

| Payload shape | Example |
|---|---|
| Explicit profile array | `{"profile": [{"t": "2026-06-11T15:00Z", "value": 100}, …]}` |
| Single timestamped value | `{"timestamp": "…", "value": 42.5}` |
| Single scalar dict | `{"setpoint": 100}` or `{"power": 250}` |
| Plain list | `[10, 20, 30, …]` |
| Bare number | `123.4` |
