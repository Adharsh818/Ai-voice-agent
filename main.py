import asyncio

import pyttsx3
import sounddevice as sd
from scipy.io.wavfile import write
import whisper

from ai_engine import async_get_ai_response, SessionState

# Load Whisper model once (using "base.en" for better accuracy)
print("Loading Whisper Speech-to-Text model (base.en)...")
whisper_model = whisper.load_model("base.en")

fs = 16000  # 16kHz sample rate (sufficient for voice)
seconds = 5  # Recording duration per turn


def speak(text):
    print("AI:", text)
    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 175)
        engine.setProperty('volume', 1.0)

        # Check and set female voice if available
        voices = engine.getProperty('voices')
        for voice in voices:
            if "female" in voice.name.lower() or "zira" in voice.name.lower() or "hazel" in voice.name.lower():
                engine.setProperty('voice', voice.id)
                break

        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception as e:
        print(f"TTS Error: {e}")


def main():
    # One event loop and one session for the whole call. Reusing a single loop
    # keeps the async engine's Gemini client from being rebound each turn, and a
    # single SessionState replaces the old module-global `state`.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session = SessionState()

    def respond(user_text):
        return loop.run_until_complete(async_get_ai_response(user_text, session))

    try:
        # 1. Output the initial greeting
        speak(respond(""))

        silence_attempts = 0

        while True:
            # Check if conversation was closed by AI
            if session.closed_conversation:
                break

            try:
                input("\n[Press Enter to start speaking (5s)...]")
            except (KeyboardInterrupt, EOFError):
                print("\nExiting program.")
                break

            # Step 1: Record audio
            print("Listening... Speak now!")
            audio = sd.rec(int(seconds * fs), samplerate=fs, channels=1)
            sd.wait()

            # Save audio to temporary WAV file
            audio_filename = "audio.wav"
            write(audio_filename, fs, audio)
            print("Processing speech...")

            # Step 2: Speech to Text (transcribe)
            try:
                result = whisper_model.transcribe(audio_filename, language="en", fp16=False)
                user_text = result.get("text", "").strip()
            except Exception as e:
                print(f"Transcription error: {e}")
                user_text = ""

            # Step 3: Handle silence or unclear audio
            if not user_text:
                silence_attempts += 1
                print(f"(No speech detected, attempt {silence_attempts}/3)")

                if silence_attempts >= 3:
                    speak(
                        "I'm sorry, I'm having trouble hearing you. "
                        "Please call us back later when you have a clearer connection. "
                        "Thank you for choosing Pearl Dental Clinic. Have a wonderful day."
                    )
                    break
                else:
                    speak("Sorry, I didn't catch that. Could you please repeat?")
                    continue

            # Speech detected, reset silence counter
            silence_attempts = 0
            print("You said:", user_text)

            # Step 4 + 5: AI response, then speak it
            speak(respond(user_text))

            # Check if conversation was closed
            if session.closed_conversation:
                break
    finally:
        loop.close()


if __name__ == "__main__":
    main()
