"""
Jarvis V2 — Voice AI Server
FastAPI backend: receives speech text, thinks with Claude Haiku,
speaks with ElevenLabs, controls browser with Playwright.
"""

import asyncio
import base64
import json
import os
import re
import time

import anthropic
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

import edge_tts

ANTHROPIC_API_KEY = config["anthropic_api_key"]
EDGE_TTS_VOICE = config.get("edge_tts_voice", "en-GB-RyanNeural")
USER_NAME = config.get("user_name", "Julian")
USER_ADDRESS = config.get("user_address", "Sir")
CITY = config.get("city", "Hamburg")
TASKS_FILE = config.get("obsidian_inbox_path", "")
SPOTIFY_CLIENT_ID = config.get("spotify_client_id", "")
SPOTIFY_CLIENT_SECRET = config.get("spotify_client_secret", "")

ai = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
http = httpx.AsyncClient(timeout=30)

app = FastAPI()

import browser_tools
import screen_capture
import spotify_tools
import govee_tools

GOVEE_API_KEY = config.get("govee_api_key", "")

if SPOTIFY_CLIENT_ID:
    spotify_tools.init(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)

if GOVEE_API_KEY:
    govee_tools.init(GOVEE_API_KEY)


@app.on_event("startup")
async def startup_event():
    if GOVEE_API_KEY:
        await govee_tools._fetch_devices()


def get_weather_sync():
    """Fetch raw weather data at startup."""
    import urllib.request
    try:
        req = urllib.request.Request(f"https://wttr.in/{CITY}?format=j1", headers={"User-Agent": "curl"})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        c = data["current_condition"][0]
        temp_f = round(int(c["temp_C"]) * 9 / 5 + 32)
        feels_f = round(int(c["FeelsLikeC"]) * 9 / 5 + 32)
        return {
            "temp": temp_f,
            "feels_like": feels_f,
            "description": c["weatherDesc"][0]["value"],
            "humidity": c["humidity"],
            "wind_kmh": c["windspeedKmph"],
        }
    except:
        return None


def get_tasks_sync():
    """Read open tasks from Obsidian (sync)."""
    if not TASKS_FILE:
        return []
    try:
        tasks_path = os.path.join(TASKS_FILE, "Tasks.md")
        with open(tasks_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [l.strip().replace("- [ ]", "").strip() for l in lines if l.strip().startswith("- [ ]")]
    except:
        return []


def refresh_data():
    """Refresh weather and tasks."""
    global WEATHER_INFO, TASKS_INFO
    WEATHER_INFO = get_weather_sync()
    TASKS_INFO = get_tasks_sync()
    print(f"[jarvis] Wetter: {WEATHER_INFO}", flush=True)
    print(f"[jarvis] Tasks: {len(TASKS_INFO)} geladen", flush=True)

WEATHER_INFO = ""
TASKS_INFO = []
refresh_data()

# Action parsing
ACTION_PATTERN = re.compile(r'\[ACTION:(\w+)\]\s*(.*?)$', re.DOTALL | re.MULTILINE)

conversations: dict[str, list] = {}

def build_system_prompt():
    weather_block = ""
    if WEATHER_INFO:
        w = WEATHER_INFO
        weather_block = f"\nWeather in {CITY}: {w['temp']}°F, feels like {w['feels_like']}°F, {w['description']}"

    task_block = ""
    if TASKS_INFO:
        task_block = f"\nOpen tasks ({len(TASKS_INFO)}): " + ", ".join(TASKS_INFO[:5])

    govee_block = ""
    if GOVEE_API_KEY and govee_tools._devices:
        names = [d.get("deviceName", d["device"]) for d in govee_tools._devices]
        govee_block = f"\nGovee lights (use exact names): {', '.join(names)}"

    return f"""You are Jarvis, the AI assistant of Tony Stark from Iron Man. Your master is {USER_NAME}. You speak exclusively English. Always address them as "{USER_ADDRESS}". Your tone is dry, sarcastic, and British-polite. Keep ALL responses to 1-2 sentences maximum — be sharp and efficient, never verbose.

IMPORTANT: NEVER write stage directions or tags in square brackets like [sarcastic] [formal] etc. Everything you write will be read aloud.

You control {USER_NAME}'s browser and Spotify. Always act immediately — never ask permission.

ACTIONS - append ONE action at the END of your response. Text before it is spoken, the action runs silently.
[ACTION:SEARCH] search term - search the web
[ACTION:OPEN] url - open a URL
[ACTION:SCREEN] - describe the screen. Write ONLY "[ACTION:SCREEN]", no text before it.
[ACTION:NEWS] - get world news. Say "Let me check the news." before it.
[ACTION:SPOTIFY] query - play music. Use for any music/play/song/artist request. query = artist, song, or genre.
[ACTION:SPOTIFY] pause - pause playback.
[ACTION:SPOTIFY] resume - resume playback.
[ACTION:SPOTIFY] skip - skip to next track.
[ACTION:SPOTIFY] volume 80 - set Spotify volume (0-100). Use for any "turn up", "lower", "volume" request.
[ACTION:GOVEE] on [device name] - turn light(s) on. device name is optional; omit to control all lights.
[ACTION:GOVEE] off [device name] - turn light(s) off.
[ACTION:GOVEE] brightness 50 [device name] - set brightness 0-100.
[ACTION:GOVEE] color red [device name] - set a named color (red/green/blue/white/purple/orange/pink/cyan/yellow).
Use partial device names to target multiple (e.g. "bedroom" matches all bedroom lights).

WHEN {USER_NAME} says "Jarvis activate": greet them in ONE sentence that includes the time of day and a quick weather mention. Be witty.

=== CURRENT DATA ===
Current time in {CITY}: {{time}}{weather_block}{task_block}{govee_block}
==="""


def get_system_prompt():
    return build_system_prompt().replace("{time}", time.strftime("%I:%M %p"))


def extract_action(text: str):
    match = ACTION_PATTERN.search(text)
    if match:
        clean = text[:match.start()].strip()
        return clean, {"type": match.group(1), "payload": match.group(2).strip()}
    return text, None


async def synthesize_speech(text: str) -> bytes:
    if not text.strip():
        return b""
    try:
        communicate = edge_tts.Communicate(text, EDGE_TTS_VOICE, rate="+15%")
        audio_parts = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_parts.append(chunk["data"])
        result = b"".join(audio_parts)
        print(f"  TTS (edge): {len(result)} bytes", flush=True)
        return result
    except Exception as e:
        print(f"  TTS EXCEPTION: {e}", flush=True)
        return b""


async def execute_action(action: dict) -> str:
    t = action["type"]
    p = action["payload"]

    if t == "SEARCH":
        result = await browser_tools.search_and_read(p)
        if "error" not in result:
            return f"Seite: {result.get('title', '')}\nURL: {result.get('url', '')}\n\n{result.get('content', '')[:2000]}"
        return f"Suche fehlgeschlagen: {result.get('error', '')}"

    elif t == "BROWSE":
        result = await browser_tools.visit(p)
        if "error" not in result:
            return f"Seite: {result.get('title', '')}\n\n{result.get('content', '')[:2000]}"
        return f"Seite nicht erreichbar: {result.get('error', '')}"

    elif t == "OPEN":
        await browser_tools.open_url(p)
        return f"Opened: {p}"

    elif t == "SCREEN":
        return await screen_capture.describe_screen(ai)

    elif t == "NEWS":
        result = await browser_tools.fetch_news()
        return result

    elif t == "SPOTIFY":
        p_lower = p.lower().strip()
        if p_lower == "pause":
            return await spotify_tools.pause()
        elif p_lower == "resume":
            return await spotify_tools.resume()
        elif p_lower == "skip":
            return await spotify_tools.skip()
        elif p_lower.startswith("volume"):
            try:
                pct = int(p_lower.replace("volume", "").strip())
            except ValueError:
                pct = 70
            return await spotify_tools.set_volume(pct)
        else:
            return await spotify_tools.search_and_play(p)

    elif t == "GOVEE":
        tokens = p.lower().strip().split()
        if not tokens:
            return ""
        cmd = tokens[0]
        COLOR_MAP = {
            "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
            "white": (255, 255, 255), "purple": (128, 0, 128),
            "orange": (255, 100, 0), "pink": (255, 20, 147),
            "cyan": (0, 255, 255), "yellow": (255, 200, 0),
        }
        if cmd == "on":
            hint = " ".join(tokens[1:]) or None
            return await govee_tools.turn_on(hint)
        elif cmd == "off":
            hint = " ".join(tokens[1:]) or None
            return await govee_tools.turn_off(hint)
        elif cmd == "brightness":
            try:
                lvl = int(tokens[1]) if len(tokens) > 1 else 50
            except ValueError:
                lvl = 50
            hint = " ".join(tokens[2:]) or None
            return await govee_tools.set_brightness(lvl, hint)
        elif cmd == "color":
            color_name = tokens[1] if len(tokens) > 1 else "white"
            hint = " ".join(tokens[2:]) or None
            rgb = COLOR_MAP.get(color_name, (255, 255, 255))
            return await govee_tools.set_color(*rgb, hint)
        return ""


async def process_message(session_id: str, user_text: str, ws: WebSocket):
    """Process message and send responses via WebSocket."""
    if session_id not in conversations:
        conversations[session_id] = []

    # Refresh weather + tasks on activate
    if "activate" in user_text.lower():
        refresh_data()

    conversations[session_id].append({"role": "user", "content": user_text})
    history = conversations[session_id][-16:]

    # LLM call
    response = await ai.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=get_system_prompt(),
        messages=history,
    )
    reply = response.content[0].text
    print(f"  LLM raw: {reply[:200]}", flush=True)
    spoken_text, action = extract_action(reply)

    # Speak the main response immediately
    if spoken_text:
        audio = await synthesize_speech(spoken_text)
        print(f"  Jarvis: {spoken_text[:80]}", flush=True)
        print(f"  Audio bytes: {len(audio)}", flush=True)
        conversations[session_id].append({"role": "assistant", "content": spoken_text})
        await ws.send_json({
            "type": "response",
            "text": spoken_text,
            "audio": base64.b64encode(audio).decode("utf-8") if audio else "",
        })

    # Execute action if any
    if action:
        print(f"  Action: {action['type']} -> {action['payload'][:100]}", flush=True)

        # Quick voice feedback for SCREEN so user knows Jarvis is working
        if action["type"] == "SCREEN":
            hint = "Let me take a look at your screen."
            hint_audio = await synthesize_speech(hint)
            await ws.send_json({
                "type": "response",
                "text": hint,
                "audio": base64.b64encode(hint_audio).decode("utf-8") if hint_audio else "",
            })

        try:
            action_result = await execute_action(action)
            print(f"  Result: {action_result}", flush=True)
        except Exception as e:
            print(f"  Action error: {e}", flush=True)
            action_result = f"Fehler: {e}"

        if action["type"] == "OPEN":
            return

        # SPOTIFY — give short spoken feedback
        if action["type"] == "SPOTIFY":
            if action_result.startswith("playing:"):
                name = action_result[len("playing:"):].strip()
                summary = f"Playing {name}, {USER_ADDRESS}."
            elif action_result == "paused":
                summary = f"Paused, {USER_ADDRESS}."
            elif action_result == "resumed":
                summary = f"Resuming, {USER_ADDRESS}."
            elif action_result == "skipped":
                summary = f"Skipped, {USER_ADDRESS}."
            elif action_result.startswith("volume:"):
                pct = action_result.split(":")[1].strip()
                summary = f"Volume set to {pct} percent, {USER_ADDRESS}."
            elif action_result.startswith("error: no active") or action_result.startswith("error: launched") or action_result.startswith("error: could not find"):
                summary = f"I've launched Spotify for you, {USER_ADDRESS} — it should start playing in a moment."
            elif action_result.startswith("error: authorization"):
                summary = f"I need to connect to Spotify first, {USER_ADDRESS}. Check your browser."
            else:
                summary = f"Sorry, {USER_ADDRESS} — {action_result.replace('error: ', '')}."
            audio2 = await synthesize_speech(summary)
            conversations[session_id].append({"role": "assistant", "content": summary})
            await ws.send_json({"type": "response", "text": summary, "audio": base64.b64encode(audio2).decode("utf-8") if audio2 else ""})
            return

        # GOVEE — give short spoken feedback
        if action["type"] == "GOVEE":
            if action_result.startswith("on:"):
                summary = f"Lights on, {USER_ADDRESS}."
            elif action_result.startswith("off:"):
                summary = f"Lights off, {USER_ADDRESS}."
            elif action_result.startswith("brightness:"):
                summary = f"Done, {USER_ADDRESS}."
            elif action_result.startswith("color:"):
                summary = f"Done, {USER_ADDRESS}."
            elif action_result.startswith("error:"):
                summary = f"Couldn't control the lights, {USER_ADDRESS} — {action_result[6:].strip()}."
            else:
                summary = f"Done, {USER_ADDRESS}."
            audio2 = await synthesize_speech(summary)
            conversations[session_id].append({"role": "assistant", "content": summary})
            await ws.send_json({"type": "response", "text": summary, "audio": base64.b64encode(audio2).decode("utf-8") if audio2 else ""})
            return

        # SEARCH, BROWSE, SCREEN — summarize results
        if action_result and "error" not in action_result.lower()[:20]:
            summary_resp = await ai.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                system=f"You are Jarvis. Summarize the following in 1-2 sentences in Jarvis style. Address the user as {USER_ADDRESS}. No square bracket tags. No ACTION tags.",
                messages=[{"role": "user", "content": f"Summarize:\n\n{action_result}"}],
            )
            summary = summary_resp.content[0].text
            summary, _ = extract_action(summary)
        else:
            summary = f"That didn't work, {USER_ADDRESS}."

        audio2 = await synthesize_speech(summary)
        conversations[session_id].append({"role": "assistant", "content": summary})
        await ws.send_json({
            "type": "response",
            "text": summary,
            "audio": base64.b64encode(audio2).decode("utf-8") if audio2 else "",
        })


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    session_id = str(id(ws))
    print(f"[jarvis] Client connected", flush=True)

    try:
        while True:
            data = await ws.receive_json()
            user_text = data.get("text", "").strip()
            if not user_text:
                continue

            print(f"  You:    {user_text}", flush=True)
            await process_message(session_id, user_text, ws)

    except WebSocketDisconnect:
        conversations.pop(session_id, None)


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "frontend")), name="static")


@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "frontend", "index.html"))


if __name__ == "__main__":
    import uvicorn
    print("=" * 50, flush=True)
    print("  J.A.R.V.I.S. V2 Server", flush=True)
    print(f"  http://localhost:8340", flush=True)
    print("=" * 50, flush=True)
    uvicorn.run(app, host="0.0.0.0", port=8340)
