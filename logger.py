import os
import sys
import datetime
import time
import functools
import json

ADDON_DIR = os.path.dirname(__file__)
LOG_FILE = os.path.join(ADDON_DIR, "Anki_EDN_Lier_les_cartes.log")

# Load debug mode setting
try:
    with open(os.path.join(ADDON_DIR, "config.json"), encoding="utf-8") as f:
        config = json.load(f)
        DEBUG_MODE = config.get("debug", False)
except Exception:
    DEBUG_MODE = False

MAX_LOG_SIZE = 1024 * 1024  # 1 MB

def log(message, level="INFO"):
    """
    Logs a message to the addon's log file and console.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_message_file = f"[{timestamp}] [{level}] {message}\n"
    formatted_message_console = f"[EDN_Lier_les_cartes] [{timestamp}] [{level}] {message}"
    
    # Print to console (only for warnings/errors or if debug mode is enabled)
    if level != "INFO" or DEBUG_MODE:
        print(formatted_message_console, file=sys.stdout)
        sys.stdout.flush()
    
    # Suppress disk logging when DEBUG_MODE is False for INFO level
    if not DEBUG_MODE and level == "INFO":
        return

    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_SIZE:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(f"[{timestamp}] [INFO] Log truncated (exceeded 1MB cap)\n")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted_message_file)
    except Exception as e:
        print(f"Error writing to log: {e}", file=sys.stderr)

def log_error(message):
    log(message, level="ERROR")

def perf_log(func):
    """Decorator — performance logging disabled."""
    return func
