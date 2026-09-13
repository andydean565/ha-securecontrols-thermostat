# 🏠 Secure Controls Thermostat (Home Assistant Integration)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz/)
[![Add to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=securecontrols_thermostat)
![License](https://img.shields.io/github/license/yourusername/ha-securecontrols_thermostat)
![Version](https://img.shields.io/badge/version-1.0.0-blue)

A custom [Home Assistant](https://www.home-assistant.io) integration for **Secure Controls smart thermostats**, connecting via the official **Beanbag Cloud API** and **WebSocket** interface.

This integration provides cloud-based two-way communication with Secure thermostats — including temperature, humidity, power data, and remote control from Home Assistant.

---

## ✨ Features

- 🔐 Secure authentication using your Beanbag account  
- 🌡️ Temperature, humidity, and power updates every 45 seconds
- ⚙️ Control target temperature, mode, and preset  
- ⚡ Power usage telemetry (where supported)  
- 🧱 Multi-gateway support  
- 🔐 Short-lived cloud connections that do not monopolize your account session
- 🧩 Exposes native Home Assistant entities:
  - `climate` — main thermostat
  - `sensor` — humidity and power metrics

---

## 📦 Installation

### Option 1 — HACS (Recommended)
1. In Home Assistant, open **HACS → Integrations → Custom Repositories**
2. Add this repository’s URL:
   ```
   https://github.com/yourusername/ha-securecontrols_thermostat
   ```
3. Select category **Integration**
4. Install **Secure Controls Thermostat**
5. Restart Home Assistant

### Option 2 — Manual
1. Copy the folder `custom_components/securecontrols_thermostat` into your HA config directory:
   ```
   config/custom_components/securecontrols_thermostat/
   ```
2. Restart Home Assistant

---

## ⚙️ Configuration

1. Go to **Settings → Devices & Services → + Add Integration**
2. Search for **Secure Controls Thermostat**
3. Enter your **Beanbag Cloud email** and **password**
4. The integration will:
   - Authenticate using the Secure Controls API  
   - Discover your gateways and thermostats  
   - Poll the thermostat every 45 seconds using a short-lived WebSocket

[![Add to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=securecontrols_thermostat)

---

## 🧠 Technical Overview

### Authentication
Uses the Beanbag Cloud REST endpoint:
```
POST /api/UserRestAPI/LoginRequest
```
Payload includes MD5-hashed password; returns a JWT (`JT`) and Session ID (`SI`).

### WebSocket Control
All device control and telemetry use short-lived WebSocket transactions. The
integration opens a socket for one read or command and closes it as soon as the
matching response arrives. It does not keep a permanent cloud connection open.

```
wss://app.beanbag.online/api/TransactionRestAPI/ConnectWebSocket
Headers:
  Authorization: Bearer <JWT>
  Session-id: <SessionId>
Subprotocol:
  BB-BO-01
```

This polling model means Home Assistant state can be up to 45 seconds behind the
thermostat, but it leaves the WebSocket free between operations so the Secure
Controls app can sign in normally.

If signing into the Secure Controls app invalidates Home Assistant's Beanbag
session, the integration deliberately stops making cloud requests. Reload the
integration from **Settings → Devices & services** when you want Home Assistant
to sign in again. It will not automatically reclaim the session from the app.

Example telemetry payload:
```json
{
  "type": "telemetry",
  "gateway_id": "63303415198340",
  "device_id": "C0032725",
  "ambient_c": 21.3,
  "target_c": 22.0,
  "humidity": 46.5,
  "power": 1,
}
```

---

## 📁 Folder Structure

```
custom_components/securecontrols_thermostat/
├── __init__.py           # integration setup
├── api.py                # HTTP + WebSocket client
├── climate.py            # Thermostat entity
├── sensor.py             # (optional) humidity/power sensors
├── config_flow.py        # Config Flow for login
├── manifest.json
└── icon.png / logo.png
```

---

## 💡 Credits

- 🔍 **API research & understanding** inspired by [ha-securemtr](https://github.com/ha-securemtr/ha-securemtr) —  
  their work on Secure Meters protocols was invaluable in decoding this API.

---

## 🪪 License

MIT License © 2025 andrew dean

---

## 🧩 Add to Home Assistant

Click below to add the integration directly in your Home Assistant instance:

[![Add to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=securecontrols_thermostat)
