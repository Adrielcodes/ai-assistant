# J.A.R.V.I.S. — Personal AI Voice Assistant

> Double-clap. Jarvis wakes up, greets you with the weather and your tasks, answers your questions with dry British wit, controls your browser, and sees your screen.

---

## Features

- **Double-Clap Trigger** — Clap twice and your entire workspace launches: Spotify, VS Code, Obsidian, Chrome with Jarvis UI
- **Voice Conversation** — Speak freely with Jarvis through your microphone. He listens, thinks, and responds with voice
- **Sarcastic British Butler** — Jarvis speaks with the personality of Tony Stark's AI: dry, witty, and always one step ahead
- **Weather & Tasks** — On startup, Jarvis greets you with the current weather and a humorous summary of your open tasks
- **Browser Automation** — "Search for X" → Jarvis opens a real browser, navigates, reads content, and summarizes it
- **Screen Vision** — "What's on my screen?" → Jarvis takes a screenshot, analyzes it with Claude Vision, and describes what he sees
- **Spotify Control** — Control music playback via voice commands
- **Smart Lighting** — Control Govee lights through voice

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Speech Input | Web Speech API (Chrome) |
| Server | FastAPI (Python) |
| Brain | Claude Haiku (Anthropic) |
| Voice | ElevenLabs TTS |
| Browser Control | Playwright |
| Screen Vision | Claude Vision + Pillow |

---

## Setup

1. Clone the repo
2. Install dependencies: `pip install -r requirements.txt`
3. Install Playwright browser: `playwright install chromium`
4. Copy `config.example.json` to `config.json` and fill in your API keys
5. Run: `start.bat`

---

## Requirements

- Python 3.10+
- Google Chrome
- Anthropic API key
- ElevenLabs API key (for voice)
