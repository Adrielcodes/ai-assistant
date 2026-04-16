"""
Jarvis Spotify Integration
OAuth2 PKCE flow + search + playback control
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".spotify_token.json")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing"

_client_id = ""
_client_secret = ""
_tokens: dict = {}

# ── Auth callback server ─────────────────────────────────────────────────────

_auth_code: str | None = None
_auth_event = threading.Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Jarvis: Spotify connected. You can close this tab.</h2></body></html>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Jarvis: Authorization failed.</h2></body></html>")
        _auth_event.set()

    def log_message(self, *args):
        pass  # Silence HTTP logs


def _run_callback_server():
    server = HTTPServer(("localhost", 8888), _CallbackHandler)
    server.timeout = 120
    server.handle_request()  # Handle exactly one request
    server.server_close()


# ── Token management ─────────────────────────────────────────────────────────

def _save_tokens(tokens: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f)


def _load_tokens() -> dict:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return {}


def _token_expired(tokens: dict) -> bool:
    return time.time() >= tokens.get("expires_at", 0) - 60


async def _refresh_access_token() -> bool:
    global _tokens
    refresh_token = _tokens.get("refresh_token")
    if not refresh_token:
        return False

    credentials = base64.b64encode(f"{_client_id}:{_client_secret}".encode()).decode()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://accounts.spotify.com/api/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
    if resp.status_code == 200:
        data = resp.json()
        _tokens["access_token"] = data["access_token"]
        _tokens["expires_at"] = time.time() + data["expires_in"]
        if "refresh_token" in data:
            _tokens["refresh_token"] = data["refresh_token"]
        _save_tokens(_tokens)
        return True
    return False


async def _get_valid_token() -> str | None:
    global _tokens
    if not _tokens:
        _tokens = _load_tokens()
    if not _tokens.get("access_token"):
        return None
    if _token_expired(_tokens):
        ok = await _refresh_access_token()
        if not ok:
            return None
    return _tokens["access_token"]


# ── Authorization ─────────────────────────────────────────────────────────────

async def authorize() -> bool:
    global _auth_code, _tokens
    _auth_event.clear()
    _auth_code = None

    state = secrets.token_urlsafe(16)
    params = urllib.parse.urlencode({
        "client_id": _client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
    })
    auth_url = f"https://accounts.spotify.com/authorize?{params}"

    # Start local callback server in background thread
    t = threading.Thread(target=_run_callback_server, daemon=True)
    t.start()

    # Try to open in Chrome specifically
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    opened = False
    for path in chrome_paths:
        if os.path.exists(path):
            import subprocess
            subprocess.Popen([path, auth_url])
            opened = True
            break
    if not opened:
        webbrowser.open(auth_url)
    print("[spotify] Opened browser for authorization...", flush=True)
    # Wait up to 120 seconds for user to authorize
    _auth_event.wait(timeout=120)

    if not _auth_code:
        print("[spotify] Authorization timed out or failed.", flush=True)
        return False

    # Exchange code for tokens
    credentials = base64.b64encode(f"{_client_id}:{_client_secret}".encode()).decode()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://accounts.spotify.com/api/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": _auth_code,
                "redirect_uri": REDIRECT_URI,
            },
        )

    if resp.status_code == 200:
        data = resp.json()
        _tokens = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": time.time() + data["expires_in"],
        }
        _save_tokens(_tokens)
        print("[spotify] Authorization successful.", flush=True)
        return True
    else:
        print(f"[spotify] Token exchange failed: {resp.text}", flush=True)
        return False


# ── Playback ──────────────────────────────────────────────────────────────────

def _open_in_chrome(url: str):
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in chrome_paths:
        if os.path.exists(path):
            import subprocess
            subprocess.Popen([path, url])
            return
    webbrowser.open(url)


async def _launch_spotify_app():
    """Try to launch the Spotify desktop app."""
    import subprocess
    spotify_paths = [
        os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe"),
        r"C:\Program Files\WindowsApps\SpotifyAB.SpotifyMusic_Spotify.exe",
    ]
    for path in spotify_paths:
        if os.path.exists(path):
            subprocess.Popen([path])
            print(f"[spotify] Launched Spotify from {path}", flush=True)
            await asyncio.sleep(5)
            return
    # Fallback: URI scheme via Windows shell
    subprocess.Popen(["cmd", "/c", "start", "spotify:"], creationflags=subprocess.CREATE_NO_WINDOW)
    print("[spotify] Launched Spotify via URI scheme", flush=True)
    await asyncio.sleep(5)


async def _get_device_id(token: str) -> str | None:
    """Get a usable device ID — active first, otherwise any available device."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.spotify.com/v1/me/player/devices",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        return None
    devices = resp.json().get("devices", [])
    if not devices:
        return None
    # Prefer active device
    for d in devices:
        if d.get("is_active"):
            return d["id"]
    return devices[0]["id"]


async def _play_with_device(token: str, payload: dict) -> int:
    """Transfer playback to a device if needed, then play. Returns HTTP status."""
    device_id = await _get_device_id(token)

    if device_id is None:
        # Launch Spotify and wait for it to register a device
        await _launch_spotify_app()
        token_new = await _get_valid_token()
        if token_new:
            token = token_new
        device_id = await _get_device_id(token)

    if device_id is None:
        return 404

    # Transfer playback to this device (activates it even if idle)
    async with httpx.AsyncClient(timeout=10) as client:
        await client.put(
            "https://api.spotify.com/v1/me/player",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"device_ids": [device_id], "play": False},
        )
    await asyncio.sleep(1)

    # Now play with device_id explicitly
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(
            f"https://api.spotify.com/v1/me/player/play?device_id={device_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
    return resp.status_code


async def search_and_play(query: str) -> str:
    """Search for a track/artist/playlist and play it. Returns status message."""
    token = await _get_valid_token()
    if not token:
        ok = await authorize()
        if not ok:
            return "error: authorization failed"
        token = await _get_valid_token()
        if not token:
            return "error: could not get token after auth"

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": query, "type": "track,artist,playlist", "limit": 5},
        )

    if resp.status_code != 200:
        return f"error: search failed ({resp.status_code})"

    results = resp.json()
    tracks = results.get("tracks", {}).get("items", [])
    artists = results.get("artists", {}).get("items", [])
    playlists = results.get("playlists", {}).get("items", [])

    context_uri = None
    track_uris = None
    name = ""

    if tracks:
        track = tracks[0]
        track_uris = [track["uri"]]
        name = f"{track['name']} by {track['artists'][0]['name']}"
    elif artists:
        artist = artists[0]
        context_uri = artist["uri"]
        name = artist["name"]
    elif playlists:
        playlist = playlists[0]
        context_uri = playlist["uri"]
        name = playlist["name"]
    else:
        return f"error: nothing found for '{query}'"

    payload = {}
    if context_uri:
        payload["context_uri"] = context_uri
    elif track_uris:
        payload["uris"] = track_uris

    token = await _get_valid_token()
    status = await _play_with_device(token, payload)

    if status in (200, 204):
        return f"playing: {name}"
    elif status == 404:
        return "error: could not find a Spotify device even after launching — make sure Spotify is installed and you are logged in"
    elif status == 403:
        return "error: Spotify Premium is required for playback control"
    else:
        return f"error: playback failed ({status})"


async def pause() -> str:
    token = await _get_valid_token()
    if not token:
        return "error: not authorized"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(
            "https://api.spotify.com/v1/me/player/pause",
            headers={"Authorization": f"Bearer {token}"},
        )
    return "paused" if resp.status_code in (200, 204) else f"error: {resp.status_code}"


async def resume() -> str:
    token = await _get_valid_token()
    if not token:
        return "error: not authorized"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(
            "https://api.spotify.com/v1/me/player/play",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
    return "resumed" if resp.status_code in (200, 204) else f"error: {resp.status_code}"


async def skip() -> str:
    token = await _get_valid_token()
    if not token:
        return "error: not authorized"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.spotify.com/v1/me/player/next",
            headers={"Authorization": f"Bearer {token}"},
        )
    return "skipped" if resp.status_code in (200, 204) else f"error: {resp.status_code}"


async def set_volume(percent: int) -> str:
    token = await _get_valid_token()
    if not token:
        return "error: not authorized"
    percent = max(0, min(100, percent))
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(
            f"https://api.spotify.com/v1/me/player/volume?volume_percent={percent}",
            headers={"Authorization": f"Bearer {token}"},
        )
    return f"volume: {percent}" if resp.status_code in (200, 204) else f"error: {resp.status_code}"


def init(client_id: str, client_secret: str):
    global _client_id, _client_secret, _tokens
    _client_id = client_id
    _client_secret = client_secret
    _tokens = _load_tokens()
    print(f"[spotify] Initialized. Token on disk: {'yes' if _tokens.get('access_token') else 'no'}", flush=True)
