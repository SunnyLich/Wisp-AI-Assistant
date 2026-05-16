"""
config.py — Central configuration loaded from .env
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

# --- LLM ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")       # groq | openai | anthropic
LLM_MODEL = os.getenv("LLM_MODEL", "llama3-8b-8192")

# --- Chat / elaborate LLM (defaults to same as above) ---
CHAT_LLM_PROVIDER = os.getenv("CHAT_LLM_PROVIDER", LLM_PROVIDER)
CHAT_LLM_MODEL    = os.getenv("CHAT_LLM_MODEL",    LLM_MODEL)

# --- TTS ---
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "cartesia")    # cartesia | elevenlabs | none
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "")

# --- App behaviour ---
DOLL_AUTO_HIDE = os.getenv("DOLL_AUTO_HIDE", "true").lower() == "true"  # hide doll when idle
CHAT_AUTO_ELABORATE = os.getenv("CHAT_AUTO_ELABORATE", "true").lower() == "true"  # auto-send elaborate prompt on chat open
CHAT_ELABORATE_PROMPT = os.getenv("CHAT_ELABORATE_PROMPT", "Please elaborate on that.")

# --- Hotkeys ---
HOTKEY_INVOKE         = os.getenv("HOTKEY_INVOKE",         "ctrl+q")
HOTKEY_ADD_CONTEXT    = os.getenv("HOTKEY_ADD_CONTEXT",    "alt+q")   # add selected text to context buffer
HOTKEY_CLEAR_CONTEXT  = os.getenv("HOTKEY_CLEAR_CONTEXT",  "alt+w")   # clear context buffer

# Intent shortcut mapping: ctrl+q then a key
# `label`  — shown on the on-screen picker overlay
# `prompt` — the actual instruction sent to the LLM (can be more detailed)
# `key`    — keyboard key that triggers this direction (single letter)
INTENT_SHORTCUTS = {
    "up": {
        "label":  os.getenv("INTENT_UP_LABEL",   "What is this?"),
        "prompt": os.getenv("INTENT_UP_PROMPT",  "What is this? Give me a clear, plain-English explanation in 2-3 sentences."),
        "key":    os.getenv("INTENT_UP_KEY",     "w"),
    },
    "down": {
        "label":  os.getenv("INTENT_DOWN_LABEL",  "Custom prompt"),
        "prompt": os.getenv("INTENT_DOWN_PROMPT", ""),   # unused — user types their own
        "key":    os.getenv("INTENT_DOWN_KEY",    "s"),
    },
    "left": {
        "label":  os.getenv("INTENT_LEFT_LABEL",  "Explain simply"),
        "prompt": os.getenv("INTENT_LEFT_PROMPT", "Explain this as simply as possible. Assume I have no technical background whatsoever."),
        "key":    os.getenv("INTENT_LEFT_KEY",    "a"),
    },
    "right": {
        "label":  os.getenv("INTENT_RIGHT_LABEL",  "How do I fix this?"),
        "prompt": os.getenv("INTENT_RIGHT_PROMPT", "How do I fix this? Give me: 1, what error is this in 1 sentence; 2, concise, actionable steps I can follow right now."),
        "key":    os.getenv("INTENT_RIGHT_KEY",   "d"),
    },
}

# --- UI sizes ---
BUBBLE_WIDTH = int(os.getenv("BUBBLE_WIDTH", "340"))   # px wide (not including tail)
BUBBLE_LINES = int(os.getenv("BUBBLE_LINES", "2"))     # max lines shown at once
DOLL_SIZE    = int(os.getenv("DOLL_SIZE",    "80"))    # doll icon size px (square)

# --- Audio ---
FILLER_AUDIO_DIR = os.path.join(os.path.dirname(__file__), "assets", "filler")
FILLER_MAX_DURATION_MS = 1000   # filler clips must be < 1s

# --- Latency targets (ms) ---
LATENCY_TARGET_MS = 1500
LATENCY_CEILING_MS = 3000

# --- System prompt ---
SYSTEM_PROMPT_UTILITY = os.getenv(
    "SYSTEM_PROMPT_UTILITY",
    "You are a concise desktop assistant. "
    "Answer in 1-3 short sentences. Be direct and plain. No markdown."
)

def get_system_prompt() -> str:
    return SYSTEM_PROMPT_UTILITY
