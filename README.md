# Secure Controls Thermostat (Home Assistant)

Custom integration for thermostats controlled by the Secure Controls app.

> Status: scaffolding / starter project. API calls are stubbed in `api.py` — wire them
> to the vendor endpoints or your proxy-captured requests.

## Quick start

1. Copy `custom_components/securecontrols_thermo/` into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant.
3. Add integration: **Settings → Devices & Services → Add Integration → Secure Controls Thermostat**.
4. Enter your account credentials; select the discovered thermostat(s).

## Development

- Python: 3.12+
- Home Assistant Core dev container or local venv
- Lint: ruff; Type check: mypy (optional)

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements-dev.txt
pytest -q
ruff check .
```

## Repo layout

```
custom_components/securecontrols_thermo/
  ├── __init__.py
  ├── manifest.json
  ├── const.py
  ├── config_flow.py
  ├── coordinator.py
  ├── api.py
  ├── climate.py
  ├── diagnostics.py
  ├── services.yaml
  └── translations/
      ├── en.json
      └── strings.json
```

## License

MIT
