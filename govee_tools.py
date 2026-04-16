"""
Govee smart light control via Govee Open API v2.
Get your API key: Govee Home app → Profile → About Us → Apply for API Key
"""

import uuid
import httpx

BASE_URL = "https://openapi.api.govee.com"
_api_key: str = ""
_devices: list = []  # cached list from /router/api/v1/user/devices


def init(api_key: str):
    global _api_key
    _api_key = api_key


def _headers():
    return {
        "Govee-API-Key": _api_key,
        "Content-Type": "application/json",
    }


async def _fetch_devices() -> list:
    global _devices
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BASE_URL}/router/api/v1/user/devices", headers=_headers())
            r.raise_for_status()
            data = r.json()
            _devices = data.get("data", [])
            print(f"[govee] Fetched {len(_devices)} device(s): {[d.get('deviceName') for d in _devices]}", flush=True)
    except Exception as e:
        print(f"[govee] Failed to fetch devices: {e}", flush=True)
        _devices = []
    return _devices


def _find_devices(name_hint: str | None) -> list[dict]:
    """Return all matching devices. If no hint or 'all', return all devices."""
    if not _devices:
        return []
    if not name_hint or name_hint.strip().lower() in ("all", ""):
        return _devices
    hint = name_hint.strip().lower()
    matches = [d for d in _devices if hint in d.get("deviceName", "").lower()]
    return matches if matches else _devices  # fallback to all if no match


async def _control(sku: str, device: str, cap_type: str, instance: str, value) -> bool:
    """Send a capability control command. Returns True on success."""
    payload = {
        "requestId": str(uuid.uuid4()),
        "payload": {
            "sku": sku,
            "device": device,
            "capability": {
                "type": cap_type,
                "instance": instance,
                "value": value,
            },
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{BASE_URL}/router/api/v1/device/control",
                headers=_headers(),
                json=payload,
            )
            r.raise_for_status()
            return True
    except Exception as e:
        print(f"[govee] Control error for {device}: {e}", flush=True)
        return False


async def turn_on(name_hint: str | None = None) -> str:
    if not _devices:
        await _fetch_devices()
    devices = _find_devices(name_hint)
    if not devices:
        return "error: no Govee devices found"
    results = []
    for d in devices:
        ok = await _control(d["sku"], d["device"], "devices.capabilities.on_off", "powerSwitch", 1)
        results.append((d.get("deviceName", d["device"]), ok))
    failed = [n for n, ok in results if not ok]
    if failed:
        return f"error: failed to turn on {', '.join(failed)}"
    return f"on: {', '.join(n for n, _ in results)}"


async def turn_off(name_hint: str | None = None) -> str:
    if not _devices:
        await _fetch_devices()
    devices = _find_devices(name_hint)
    if not devices:
        return "error: no Govee devices found"
    results = []
    for d in devices:
        ok = await _control(d["sku"], d["device"], "devices.capabilities.on_off", "powerSwitch", 0)
        results.append((d.get("deviceName", d["device"]), ok))
    failed = [n for n, ok in results if not ok]
    if failed:
        return f"error: failed to turn off {', '.join(failed)}"
    return f"off: {', '.join(n for n, _ in results)}"


async def set_brightness(level: int, name_hint: str | None = None) -> str:
    """level: 1-100"""
    if not _devices:
        await _fetch_devices()
    devices = _find_devices(name_hint)
    if not devices:
        return "error: no Govee devices found"
    level = max(1, min(100, level))
    for d in devices:
        await _control(d["sku"], d["device"], "devices.capabilities.range", "brightness", level)
    names = [d.get("deviceName", d["device"]) for d in devices]
    return f"brightness: {level}% on {', '.join(names)}"


async def set_color(r: int, g: int, b: int, name_hint: str | None = None) -> str:
    """r, g, b: 0-255. Govee colorRgb uses a single integer: r*65536 + g*256 + b"""
    if not _devices:
        await _fetch_devices()
    devices = _find_devices(name_hint)
    if not devices:
        return "error: no Govee devices found"
    rgb_int = r * 65536 + g * 256 + b
    for d in devices:
        await _control(d["sku"], d["device"], "devices.capabilities.color_setting", "colorRgb", rgb_int)
    names = [d.get("deviceName", d["device"]) for d in devices]
    return f"color: rgb({r},{g},{b}) on {', '.join(names)}"
