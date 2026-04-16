// Jarvis V2 — Frontend
const orb = document.getElementById('orb');
const status = document.getElementById('status');
const transcript = document.getElementById('transcript');

let ws;
let audioQueue = [];
let isPlaying = false;
let audioUnlocked = false;

// Unlock audio on ANY user interaction
function unlockAudio() {
    if (!audioUnlocked) {
        const silent = new Audio('data:audio/mp3;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWGluZwAAAA8AAAACAAABhgC7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7//////////////////////////////////////////////////////////////////8AAAAATGF2YzU4LjEzAAAAAAAAAAAAAAAAJAAAAAAAAAAAAYZNIGPkAAAAAAAAAAAAAAAAAAAA//tQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWGluZwAAAA8AAAACAAABhgC7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7//////////////////////////////////////////////////////////////////8AAAAATGF2YzU4LjEzAAAAAAAAAAAAAAAAJAAAAAAAAAAAAYZNIGPkAAAAAAAAAAAAAAAAAAAA');
        silent.play().then(() => {
            audioUnlocked = true;
            console.log('[jarvis] Audio unlocked');
        }).catch(() => {});
    }
}
document.addEventListener('click', unlockAudio, { once: false });
document.addEventListener('touchstart', unlockAudio, { once: false });
document.addEventListener('keydown', unlockAudio, { once: false });

function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => {
        console.log('[jarvis] WebSocket connected');
        status.textContent = 'Click anywhere to start.';
        setOrbState('thinking');
        ws.send(JSON.stringify({ text: 'Jarvis activate' }));
    };
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'response') {
            addTranscript('jarvis', data.text);
            if (data.audio && data.audio.length > 0) {
                queueAudio(data.audio);
            } else {
                setOrbState('idle');
                setTimeout(startListening, 500);
            }
        } else if (data.type === 'status') {
            status.textContent = data.text;
        }
    };
    ws.onclose = () => {
        status.textContent = 'Connection lost. Reconnecting...';
        setTimeout(connect, 3000);
    };
}

let currentAudio = null;

function stopAudio() {
    if (currentAudio) {
        currentAudio.onended = null;
        currentAudio.onerror = null;
        currentAudio.pause();
        currentAudio = null;
    }
    audioQueue = [];
    isPlaying = false;
}

function queueAudio(base64Audio) {
    audioQueue.push(base64Audio);
    if (!isPlaying) playNext();
}

function playNext() {
    if (audioQueue.length === 0) {
        isPlaying = false;
        currentAudio = null;
        exitCommandMode();
        setOrbState('listening');
        status.textContent = 'Say "Jarvis" to give a command.';
        setTimeout(startListening, 1200);  // Wait for speaker echo to die down
        return;
    }
    isPlaying = true;
    setOrbState('speaking');
    status.textContent = '';
    // Listen during playback — only wake word will trigger interrupt
    setTimeout(startListening, 400);

    const b64 = audioQueue.shift();
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const blob = new Blob([bytes], { type: 'audio/mpeg' });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    currentAudio = audio;
    audio.onended = () => { URL.revokeObjectURL(url); currentAudio = null; playNext(); };
    audio.onerror = () => { URL.revokeObjectURL(url); currentAudio = null; playNext(); };
    audio.play().catch(err => {
        console.warn('[jarvis] Autoplay blocked, waiting for click...');
        status.textContent = 'Click anywhere so Jarvis can speak.';
        setOrbState('idle');
        document.addEventListener('click', function retry() {
            document.removeEventListener('click', retry);
            audio.play().then(() => {
                setOrbState('speaking');
                status.textContent = '';
            }).catch(() => playNext());
        });
    });
}

// Speech Recognition
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition;
let isListening = false;

// Wake word state
let waitingForCommand = false;
let wakeTimer = null;

function enterCommandMode() {
    waitingForCommand = true;
    setOrbState('listening');
    status.textContent = 'Listening...';
    if (wakeTimer) clearTimeout(wakeTimer);
    wakeTimer = setTimeout(() => {
        waitingForCommand = false;
        if (!isPlaying) status.textContent = 'Say "Jarvis" to give a command.';
    }, 8000);
}

function exitCommandMode() {
    waitingForCommand = false;
    if (wakeTimer) { clearTimeout(wakeTimer); wakeTimer = null; }
}

function createRecognition() {
    const r = new SpeechRecognition();
    r.lang = 'en-US';
    r.continuous = false;
    r.interimResults = false;
    r.maxAlternatives = 1;

    r.onresult = (event) => {
        const last = event.results[event.results.length - 1];
        if (!last.isFinal) return;
        const text = last[0].transcript.trim();
        const confidence = last[0].confidence;
        const lower = text.toLowerCase();
        const hasWake = lower.includes('jarvis');

        // During playback: only react to wake word to interrupt
        if (isPlaying) {
            if (hasWake && confidence > 0.4) {
                stopAudio();
                isListening = false;
                enterCommandMode();
            }
            return;
        }

        // Idle mode: require wake word OR already waiting for command
        if (!waitingForCommand && !hasWake) return;
        if (!text || text.length <= 1 || confidence <= 0.4) return;

        // Strip wake word, get the actual command
        let command = hasWake ? text.replace(/\bjarvis[,!.\s]*/gi, '').trim() : text;

        if (command.length > 1) {
            exitCommandMode();
            isListening = false;
            addTranscript('user', text);
            setOrbState('thinking');
            status.textContent = 'Jarvis is thinking...';
            ws.send(JSON.stringify({ text: command }));
        } else if (hasWake) {
            // Said just "Jarvis" — wait for follow-up command
            isListening = false;
            enterCommandMode();
        }
    };

    r.onend = () => {
        isListening = false;
        // Always restart — during playback we only respond to wake word
        const delay = isPlaying ? 300 : 400;
        setTimeout(startListening, delay);
    };

    r.onerror = (event) => {
        isListening = false;
        if (event.error === 'no-speech' || event.error === 'aborted') {
            const delay = isPlaying ? 300 : 400;
            setTimeout(startListening, delay);
        } else {
            setTimeout(startListening, 1000);
        }
    };

    return r;
}

if (SpeechRecognition) {
    recognition = createRecognition();
}

function startListening() {
    if (isListening) return;
    try {
        recognition = createRecognition();
        recognition.start();
        isListening = true;
        if (!isPlaying) {
            setOrbState('listening');
            if (!waitingForCommand) status.textContent = 'Say "Jarvis" to give a command.';
        }
    } catch(e) {}
}

orb.addEventListener('click', () => {
    if (isPlaying) return;
    if (isListening) {
        recognition.stop();
        isListening = false;
        setOrbState('idle');
        status.textContent = 'Paused. Click to resume.';
    } else {
        startListening();
    }
});

function setOrbState(state) { orb.className = state; }

function addTranscript(role, text) {
    const div = document.createElement('div');
    div.className = role;
    div.textContent = role === 'user' ? `You: ${text}` : `Jarvis: ${text}`;
    transcript.appendChild(div);
    transcript.scrollTop = transcript.scrollHeight;
}

connect();
