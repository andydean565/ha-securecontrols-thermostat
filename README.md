# 🏠 Secure Controls Thermostat (Home Assistant Integration)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz/)
[![Add to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=securecontrols_thermo)
![License](https://img.shields.io/github/license/yourusername/ha-securecontrols_thermo)
![Version](https://img.shields.io/badge/version-1.0.0-blue)

A custom [Home Assistant](https://www.home-assistant.io) integration for **Secure Controls smart thermostats**, connecting via the official **Beanbag Cloud API** and **WebSocket** interface.

This integration enables real-time two-way communication with Secure thermostats — providing temperature, humidity, power data, and full remote control directly from Home Assistant.

---

## ✨ Features

- 🔐 Secure authentication using your Beanbag account  
- 🌡️ Real-time temperature, humidity, and power updates  
- ⚙️ Control target temperature, mode, and preset  
- ⚡ Power usage telemetry (where supported)  
- 🧱 Multi-gateway support  
- 🔄 Automatic reconnect and session management  
- 🧩 Exposes native Home Assistant entities:
  - `climate` — main thermostat
  - `sensor` — humidity and power metrics

---

## 📦 Installation

### Option 1 — HACS (Recommended)
1. In Home Assistant, open **HACS → Integrations → Custom Repositories**
2. Add this repository’s URL:
   ```
   https://github.com/yourusername/ha-securecontrols_thermo
   ```
3. Select category **Integration**
4. Install **Secure Controls Thermostat**
5. Restart Home Assistant

### Option 2 — Manual
1. Copy the folder `custom_components/securecontrols_thermo` into your HA config directory:
   ```
   config/custom_components/securecontrols_thermo/
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
   - Open a WebSocket for real-time updates  

[![Add to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=securecontrols_thermo)

---

## 🖼️ Branding

This integration includes your company logo (`icon.png`) for branding.

```
custom_components/securecontrols_thermo/
├── __init__.py
├── api.py
├── climate.py
├── manifest.json
└── icon.png
```

✅ Tips:
- `icon.png` — main integration icon (shown on “Devices & Services” tile)
- `logo.png` (optional) — larger version for setup UI
- Recommended size 256×256 or 512×512 PNG with transparent background

You can also expose it as an entity picture by placing a copy under:
```
config/www/securecontrols_thermo/icon.png
```

Then, in your entity code:
```python
self._attr_entity_picture = "/local/securecontrols_thermo/icon.png"
```

---

## 🧠 Technical Overview

### Authentication
Uses the Beanbag Cloud REST endpoint:
```
POST /api/UserRestAPI/LoginRequest
```
Payload includes MD5-hashed password; returns a JWT (`JT`) and Session ID (`SI`).

### WebSocket Control
All device control and telemetry occur over WebSocket:

```
wss://app.beanbag.online/ws
Headers:
  Authorization: Bearer <JWT>
  X-Session-Id: <SessionId>
```

The client sends and receives JSON messages for subscription, telemetry, and commands.

Example telemetry payload:
```json
{
  "type": "telemetry",
  "gateway_id": "63303415198340",
  "device_id": "C0032725",
  "ambient_c": 21.3,
  "target_c": 22.0,
  "humidity": 46.5,
  "power": 120.0,
  "heating_enabled": true
}
```

---

## 📁 Folder Structure

```
custom_components/securecontrols_thermo/
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
- 🧑‍💻 Integration developed and maintained by **[Your Name or Company]**

---

## 🪪 License

MIT License © 2025 [Your Name or Company]

---

## 🧩 Add to Home Assistant

Click below to add the integration directly in your Home Assistant instance:

[![Add to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=securecontrols_thermo)
