/**
 * Pearl Dental Clinic — Voice Agent Client
 *
 * Handles:
 *  - WebSocket connection to /ws/voice
 *  - Mic audio capture (PCM 16kHz Int16) via AudioWorklet
 *  - Audio playback (MP3 from ElevenLabs TTS)
 *  - Waveform visualization
 *  - Conversation message rendering
 *  - Barge-in detection
 */

// ============================================================
// DOM References
// ============================================================
const $connectBtn     = document.getElementById('connectBtn');
const $connectBtnText = document.getElementById('connectBtnText');
const $connectionBadge = document.getElementById('connectionBadge');
const $conversationScroll = document.getElementById('conversationScroll');
const $welcomeMessage = document.getElementById('welcomeMessage');
const $orbContainer   = document.getElementById('orbContainer');
const $statusText     = document.getElementById('statusText');
const $waveformCanvas = document.getElementById('waveformCanvas');
const $textInputGroup = document.getElementById('textInputGroup');
const $textInput      = document.getElementById('textInput');
const $sendBtn        = document.getElementById('sendBtn');
const $modeToggle     = document.getElementById('modeToggle');

// ============================================================
// State
// ============================================================
let ws = null;
let isConnected = false;
let isListening = false;
let isTextMode = false;

// Audio capture
let audioContext = null;
let micStream = null;
let workletNode = null;
let scriptProcessor = null; // ScriptProcessor fallback reference

// Audio playback
let currentAudio = null;
let mediaSource = null;
let sourceBuffer = null;
let mediaUrl = null;
let audioAppendQueue = [];
let audioStreamEnded = false;
let localSpeechFrames = 0;

// Waveform
const canvasCtx = $waveformCanvas.getContext('2d');
let analyserNode = null;
let waveformAnimId = null;

// ============================================================
// WebSocket Connection
// ============================================================
function connect() {
    if (isConnected) {
        disconnect();
        return;
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws/voice`;

    setStatus('connecting', 'Connecting...');

    // ── Pre-create AudioContext HERE, inside the user-gesture (click) handler.
    // Browsers require AudioContext to be created synchronously within a user
    // interaction; creating it later (in ws.onopen) risks autoplay suspension.
    if (!isTextMode) {
        if (!audioContext || audioContext.state === 'closed') {
            try {
                // Request 16000 Hz directly so browser does native high-quality resampling
                audioContext = new AudioContext({ sampleRate: 16000 });
                console.log(`AudioContext pre-created at 16kHz: ${audioContext.sampleRate} Hz, state: ${audioContext.state}`);
            } catch (e) {
                try {
                    audioContext = new AudioContext();
                    console.log(`AudioContext fallback: ${audioContext.sampleRate} Hz, state: ${audioContext.state}`);
                } catch (e2) {
                    console.warn('AudioContext pre-creation failed:', e2);
                }
            }
        }
        // Resume if suspended
        if (audioContext && audioContext.state === 'suspended') {
            audioContext.resume().catch(() => {});
        }
    }

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        isConnected = true;
        $connectBtn.classList.add('active');
        $connectBtnText.textContent = 'End Conversation';
        $connectionBadge.classList.add('connected');
        $connectionBadge.querySelector('.badge-text').textContent = 'Connected';
        $welcomeMessage.classList.add('hidden');
        setStatus('listening', 'Connected — Listening...');

        // Wait for the server's listening state.  The initial greeting is
        // played first, so capturing immediately would feed it into STT.
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleServerMessage(data);
        } catch (e) {
            // Binary data (shouldn't happen in our protocol)
            console.warn('Unexpected binary message');
        }
    };

    ws.onclose = () => {
        handleDisconnect();
    };

    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
        handleDisconnect();
    };
}

function disconnect() {
    stopMicCapture();
    stopAudioPlayback();

    if (ws) {
        ws.close();
        ws = null;
    }

    handleDisconnect();
}

function handleDisconnect() {
    isConnected = false;
    isListening = false;

    $connectBtn.classList.remove('active');
    $connectBtnText.textContent = 'Start Conversation';
    $connectionBadge.classList.remove('connected');
    $connectionBadge.querySelector('.badge-text').textContent = 'Disconnected';
    setStatus('idle', 'Disconnected');

    stopMicCapture();
}

// ============================================================
// Server Message Handler
// ============================================================
function handleServerMessage(data) {
    switch (data.type) {
        case 'transcript':
            handleTranscript(data.text, data.is_final);
            break;

        case 'response_text':
            addMessage('ai', data.text);
            break;

        case 'audio':
            playAudioBase64(data.data, data.format || 'mp3');
            setStatus('speaking', 'Emma is speaking...');
            $orbContainer.className = 'orb-container speaking';
            break;

        case 'audio_start':
            handleAudioStart();
            break;

        case 'audio_chunk':
            handleAudioChunk(data.data, data.format || 'mp3');
            break;

        case 'audio_end':
            handleAudioEnd();
            break;

        case 'status':
            handleStatusUpdate(data.status);
            break;

        case 'error':
            addSystemMessage(data.message || 'An error occurred.');
            break;

        default:
            console.log('Unknown message type:', data.type);
    }
}

function handleTranscript(text, isFinal) {
    if (isFinal) {
        // Show final user message
        addMessage('user', text);
        removeLiveTranscript();
    } else {
        // Show live partial transcript
        updateLiveTranscript(text);
    }
}

function handleStatusUpdate(status) {
    switch (status) {
        case 'listening':
            setStatus('listening', 'Listening...');
            $orbContainer.className = 'orb-container listening';
            isListening = true;
            if (!isTextMode && !micStream) {
                startMicCapture();
            }
            break;
        case 'processing':
            setStatus('processing', 'Processing...');
            $orbContainer.className = 'orb-container processing';
            showTypingIndicator();
            break;
        case 'speaking':
            setStatus('speaking', 'Emma is speaking...');
            $orbContainer.className = 'orb-container speaking';
            break;
    }
}

// ============================================================
// Mic Audio Capture
// ============================================================
async function startMicCapture() {
    try {
        // ── Ensure we have a live AudioContext (pre-created in connect() click handler)
        if (!audioContext || audioContext.state === 'closed') {
            // Fallback creation — may auto-suspend on some browsers
            audioContext = new AudioContext();
            console.warn('AudioContext created late (may be suspended by autoplay policy)');
        }

        // Resume if suspended
        if (audioContext.state === 'suspended') {
            await audioContext.resume();
            console.log('AudioContext resumed from suspended state');
        }

        const nativeSampleRate = audioContext.sampleRate;
        console.log(`AudioContext sample rate: ${nativeSampleRate} Hz, state: ${audioContext.state}`);

        // Request mic — use native sample rate, NOT 16kHz constraint
        micStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
                sampleRate: { ideal: nativeSampleRate },
            }
        });

        const source = audioContext.createMediaStreamSource(micStream);

        // Set up analyser for waveform visualization
        analyserNode = audioContext.createAnalyser();
        analyserNode.fftSize = 256;
        source.connect(analyserNode);

        // Try AudioWorklet first, fall back to ScriptProcessor
        let usedWorklet = false;
        try {
            await audioContext.audioWorklet.addModule('/static/audio-worklet-processor.js');
            // Pass native sample rate so worklet can downsample to 16kHz
            workletNode = new AudioWorkletNode(audioContext, 'pcm-capture-processor', {
                processorOptions: { nativeSampleRate }
            });

            workletNode.port.onmessage = (e) => {
                // The worklet posts: { type: 'audio', buffer: int16.buffer }
                // Note: the ArrayBuffer is transferred, access via e.data.buffer
                if (e.data && e.data.type === 'audio' && ws && ws.readyState === WebSocket.OPEN) {
                    handleCallerAudioRms(e.data.rms || 0);
                    try {
                        ws.send(e.data.buffer);
                    } catch (sendErr) {
                        console.warn('WebSocket send error:', sendErr);
                    }
                }
            };

            source.connect(workletNode);
            // Do NOT connect workletNode to destination — prevents mic echo
            usedWorklet = true;
            console.log('Audio capture: AudioWorklet mode with downsampling to 16kHz');
        } catch (workletErr) {
            // Fallback: ScriptProcessorNode with manual downsample
            console.warn('AudioWorklet not available, using ScriptProcessor fallback:', workletErr);
            const bufferSize = 4096;
            scriptProcessor = audioContext.createScriptProcessor(bufferSize, 1, 1);
            const ratio = nativeSampleRate / 16000;
            let phase = 0;
            let inputBuf = [];

            scriptProcessor.onaudioprocess = (e) => {
                if (!ws || ws.readyState !== WebSocket.OPEN) return;

                const float32 = e.inputBuffer.getChannelData(0);
                for (let i = 0; i < float32.length; i++) inputBuf.push(float32[i]);

                const output = [];
                while (inputBuf.length >= 2) {
                    const i0 = Math.floor(phase);
                    const i1 = i0 + 1;
                    if (i1 >= inputBuf.length) break;
                    const frac = phase - i0;
                    output.push(inputBuf[i0] * (1 - frac) + inputBuf[i1] * frac);
                    phase += ratio;
                }
                const consumed = Math.floor(phase);
                inputBuf.splice(0, consumed);
                phase -= consumed;

                if (output.length > 0) {
                    const int16 = new Int16Array(output.length);
                    for (let i = 0; i < output.length; i++) {
                        const s = Math.max(-1, Math.min(1, output[i]));
                        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                    }
                    try {
                        ws.send(int16.buffer);
                    } catch (sendErr) {
                        console.warn('WebSocket send error:', sendErr);
                    }
                }
            };

            source.connect(scriptProcessor);
            // ScriptProcessor requires connection to destination to fire onaudioprocess
            scriptProcessor.connect(audioContext.destination);
        }

        // Start waveform visualization
        startWaveformVisualization();

        isListening = true;
        console.log('Mic capture started successfully');

    } catch (err) {
        console.error('Mic access denied or failed:', err);
        if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
            addSystemMessage('Microphone access denied. Please allow mic access in your browser settings and refresh the page, or use text mode instead.');
        } else if (err.name === 'NotFoundError') {
            addSystemMessage('No microphone found. Please connect a microphone or use text mode.');
        } else {
            addSystemMessage(`Microphone error: ${err.message}. Please use text mode.`);
        }
        enableTextMode();
    }
}

function stopMicCapture() {
    if (waveformAnimId) {
        cancelAnimationFrame(waveformAnimId);
        waveformAnimId = null;
    }

    if (workletNode) {
        workletNode.disconnect();
        workletNode = null;
    }

    if (scriptProcessor) {
        scriptProcessor.disconnect();
        scriptProcessor = null;
    }

    if (audioContext) {
        audioContext.close().catch(() => {});
        audioContext = null;
    }

    if (micStream) {
        micStream.getTracks().forEach(t => t.stop());
        micStream = null;
    }

    analyserNode = null;
    isListening = false;
}

// ============================================================
// Audio Playback (Streaming Accumulator)
// ============================================================
let audioChunks = [];       // Accumulated raw byte arrays during streaming
let bargeInSent = false;
let audioFormat = 'mp3';

function handleAudioStart() {
    stopAudioPlayback(false);
    audioChunks = [];
    audioAppendQueue = [];
    audioStreamEnded = false;
    bargeInSent = false;
    audioFormat = 'mp3';
    setStatus('speaking', 'Emma is speaking...');
    $orbContainer.className = 'orb-container speaking';
    removeTypingIndicator();

    // MediaSource lets the first MP3 bytes play immediately.  The old Blob
    // approach waited for the entire TTS response, nullifying server streaming.
    if ('MediaSource' in window && MediaSource.isTypeSupported('audio/mpeg')) {
        mediaSource = new MediaSource();
        mediaUrl = URL.createObjectURL(mediaSource);
        currentAudio = new Audio(mediaUrl);
        currentAudio.volume = 1.0;
        currentAudio.onended = notifyPlaybackComplete;
        currentAudio.onerror = () => finishPlaybackWithError();
        mediaSource.addEventListener('sourceopen', () => {
            try {
                sourceBuffer = mediaSource.addSourceBuffer('audio/mpeg');
                sourceBuffer.addEventListener('updateend', flushAudioAppendQueue);
                flushAudioAppendQueue();
            } catch (err) {
                console.error('Streaming audio setup failed:', err);
                // Keep receiving chunks; the Blob fallback below can play them.
                sourceBuffer = null;
                mediaSource = null;
            }
        }, { once: true });
    }
}

function handleAudioChunk(base64Data, format = 'mp3') {
    try {
        audioFormat = format;
        const binaryString = atob(base64Data);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        audioChunks.push(bytes); // retained for an MSE compatibility fallback
        if (mediaSource) {
            audioAppendQueue.push(bytes);
            flushAudioAppendQueue();
        }
    } catch (e) {
        console.error('Audio chunk decode error:', e);
    }
}

function handleAudioEnd() {
    audioStreamEnded = true;
    if (mediaSource) {
        flushAudioAppendQueue();
        return;
    }

    // Compatibility fallback for browsers without MediaSource MP3 support.
    if (audioChunks.length === 0) {
        $orbContainer.className = 'orb-container listening';
        setStatus('listening', 'Listening...');
        return;
    }

    // Merge all chunks into one Uint8Array
    const totalLength = audioChunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const merged = new Uint8Array(totalLength);
    let offset = 0;
    for (const chunk of audioChunks) {
        merged.set(chunk, offset);
        offset += chunk.length;
    }
    audioChunks = [];

    const blob = new Blob([merged], { type: `audio/${audioFormat}` });
    const url = URL.createObjectURL(blob);

    currentAudio = new Audio(url);
    currentAudio.volume = 1.0;

    currentAudio.onended = notifyPlaybackComplete;

    currentAudio.onerror = (e) => {
        console.error('Audio playback error:', e);
        URL.revokeObjectURL(url);
        finishPlaybackWithError();
    };

    currentAudio.play().catch(err => {
        console.error('Playback failed:', err);
        addSystemMessage('Click anywhere to enable audio playback.');
    });
}

function flushAudioAppendQueue() {
    if (!sourceBuffer || sourceBuffer.updating) return;
    if (audioAppendQueue.length > 0) {
        const chunk = audioAppendQueue.shift();
        try {
            sourceBuffer.appendBuffer(chunk);
            if (currentAudio && currentAudio.paused) {
                currentAudio.play().catch(err => {
                    console.error('Streaming playback failed:', err);
                    addSystemMessage('Click anywhere to enable audio playback.');
                });
            }
        } catch (err) {
            console.error('Streaming audio append failed:', err);
        }
    } else if (audioStreamEnded && mediaSource && mediaSource.readyState === 'open') {
        try { mediaSource.endOfStream(); } catch (_) {}
    }
}

function notifyPlaybackComplete() {
    cleanupPlayback();
    $orbContainer.className = 'orb-container listening';
    setStatus('listening', 'Listening...');
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'playback_complete' }));
    }
}

function finishPlaybackWithError() {
    cleanupPlayback();
    $orbContainer.className = 'orb-container listening';
    setStatus('listening', 'Listening...');
}

function cleanupPlayback() {
    if (mediaUrl) URL.revokeObjectURL(mediaUrl);
    mediaUrl = null;
    mediaSource = null;
    sourceBuffer = null;
    audioAppendQueue = [];
    currentAudio = null;
}

function playAudioBase64(base64Data, format) {
    // Legacy single-blob path (for backward compat with 'audio' message type)
    handleAudioStart();
    handleAudioChunk(base64Data, format);
    handleAudioEnd();
}

function stopAudioPlayback(notify = true) {
    audioChunks = [];

    const hadAudio = currentAudio !== null;

    if (currentAudio) {
        currentAudio.pause();
        currentAudio.src = '';
    }
    cleanupPlayback();

    // Notify server of barge-in once per playback.
    if (notify && hadAudio && !bargeInSent && ws && ws.readyState === WebSocket.OPEN) {
        bargeInSent = true;
        ws.send(JSON.stringify({ type: 'barge_in' }));
    }
}

function handleCallerAudioRms(rms) {
    if (!currentAudio || currentAudio.paused) {
        localSpeechFrames = 0;
        return;
    }
    localSpeechFrames = rms >= 0.02 ? localSpeechFrames + 1 : 0;
    if (localSpeechFrames >= 2) {
        localSpeechFrames = 0;
        stopAudioPlayback(true);
    }
}

// ============================================================
// Waveform Visualization
// ============================================================
function startWaveformVisualization() {
    if (!analyserNode) return;

    const bufferLength = analyserNode.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const width = $waveformCanvas.width;
    const height = $waveformCanvas.height;

    function draw() {
        waveformAnimId = requestAnimationFrame(draw);

        analyserNode.getByteTimeDomainData(dataArray);

        canvasCtx.fillStyle = 'rgba(10, 10, 15, 0.3)';
        canvasCtx.fillRect(0, 0, width, height);

        // Gradient line
        const gradient = canvasCtx.createLinearGradient(0, 0, width, 0);
        gradient.addColorStop(0, 'rgba(108, 99, 255, 0.8)');
        gradient.addColorStop(0.5, 'rgba(59, 130, 246, 0.8)');
        gradient.addColorStop(1, 'rgba(6, 182, 212, 0.8)');

        canvasCtx.lineWidth = 2;
        canvasCtx.strokeStyle = gradient;
        canvasCtx.beginPath();

        const sliceWidth = width / bufferLength;
        let x = 0;

        for (let i = 0; i < bufferLength; i++) {
            const v = dataArray[i] / 128.0;
            const y = (v * height) / 2;

            if (i === 0) {
                canvasCtx.moveTo(x, y);
            } else {
                canvasCtx.lineTo(x, y);
            }
            x += sliceWidth;
        }

        canvasCtx.lineTo(width, height / 2);
        canvasCtx.stroke();

        // Glow effect
        canvasCtx.shadowBlur = 8;
        canvasCtx.shadowColor = 'rgba(108, 99, 255, 0.4)';
    }

    draw();
}

// ============================================================
// UI Message Rendering
// ============================================================
function addMessage(role, text) {
    removeLiveTranscript();
    removeTypingIndicator();

    const msgEl = document.createElement('div');
    msgEl.className = `message ${role}`;

    const avatarText = role === 'ai' ? 'E' : 'U';
    msgEl.innerHTML = `
        <div class="message-avatar">${avatarText}</div>
        <div class="message-content">${escapeHtml(text)}</div>
    `;

    $conversationScroll.appendChild(msgEl);
    scrollToBottom();
}

function addSystemMessage(text) {
    const msgEl = document.createElement('div');
    msgEl.className = 'message ai';
    msgEl.innerHTML = `
        <div class="message-avatar">⚠</div>
        <div class="message-content" style="color: var(--warning); border-color: rgba(251,191,36,0.2);">${escapeHtml(text)}</div>
    `;
    $conversationScroll.appendChild(msgEl);
    scrollToBottom();
}

function updateLiveTranscript(text) {
    let el = document.querySelector('.live-transcript');
    if (!el) {
        el = document.createElement('div');
        el.className = 'live-transcript';
        $conversationScroll.appendChild(el);
    }
    el.textContent = `🎤 ${text}`;
    scrollToBottom();
}

function removeLiveTranscript() {
    const el = document.querySelector('.live-transcript');
    if (el) el.remove();
}

function showTypingIndicator() {
    if (document.querySelector('.typing-indicator')) return;

    const msgEl = document.createElement('div');
    msgEl.className = 'message ai';
    msgEl.id = 'typingMessage';
    msgEl.innerHTML = `
        <div class="message-avatar">E</div>
        <div class="message-content">
            <div class="typing-indicator">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
        </div>
    `;
    $conversationScroll.appendChild(msgEl);
    scrollToBottom();
}

function removeTypingIndicator() {
    const el = document.getElementById('typingMessage');
    if (el) el.remove();
}

function scrollToBottom() {
    requestAnimationFrame(() => {
        $conversationScroll.scrollTop = $conversationScroll.scrollHeight;
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================
// Status & UI Helpers
// ============================================================
function setStatus(state, text) {
    $statusText.textContent = text;

    // Update status text color
    switch (state) {
        case 'listening':
            $statusText.style.color = 'var(--success)';
            break;
        case 'processing':
            $statusText.style.color = 'var(--warning)';
            break;
        case 'speaking':
            $statusText.style.color = 'var(--accent-light)';
            break;
        case 'connecting':
            $statusText.style.color = 'var(--text-muted)';
            break;
        default:
            $statusText.style.color = 'var(--text-muted)';
    }
}

function enableTextMode() {
    isTextMode = true;
    $textInputGroup.style.display = 'flex';
    $textInput.focus();
    stopMicCapture();
}

function disableTextMode() {
    isTextMode = false;
    $textInputGroup.style.display = 'none';
    if (isConnected) {
        startMicCapture();
    }
}

// ============================================================
// Event Listeners
// ============================================================
$connectBtn.addEventListener('click', connect);

$modeToggle.addEventListener('click', () => {
    if (isTextMode) {
        disableTextMode();
    } else {
        enableTextMode();
    }
});

$sendBtn.addEventListener('click', sendTextMessage);
$textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendTextMessage();
    }
});

function sendTextMessage() {
    const text = $textInput.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

    addMessage('user', text);
    ws.send(JSON.stringify({ type: 'text_input', text }));
    $textInput.value = '';
}

// Enable audio playback on any user interaction (browser autoplay policy)
// Do NOT use { once: true } — the AudioContext may be re-created on reconnect
document.addEventListener('click', () => {
    if (audioContext && audioContext.state === 'suspended') {
        audioContext.resume().then(() => {
            console.log('AudioContext resumed by user interaction');
        }).catch(() => {});
    }
});

// Handle page visibility — pause/resume mic
document.addEventListener('visibilitychange', () => {
    if (document.hidden && audioContext) {
        audioContext.suspend();
    } else if (!document.hidden && audioContext && isConnected) {
        audioContext.resume();
    }
});

// Draw initial flat waveform
function drawFlatWaveform() {
    const width = $waveformCanvas.width;
    const height = $waveformCanvas.height;
    canvasCtx.fillStyle = 'rgba(10, 10, 15, 1)';
    canvasCtx.fillRect(0, 0, width, height);
    canvasCtx.strokeStyle = 'rgba(108, 99, 255, 0.2)';
    canvasCtx.lineWidth = 1.5;
    canvasCtx.beginPath();
    canvasCtx.moveTo(0, height / 2);
    canvasCtx.lineTo(width, height / 2);
    canvasCtx.stroke();
}

drawFlatWaveform();

console.log('🎙️ Pearl Dental Voice Agent initialized');
