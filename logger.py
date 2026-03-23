import os
import sys
import datetime
import time
import functools

ADDON_DIR = os.path.dirname(__file__)
LOG_FILE = os.path.join(ADDON_DIR, "Anki_EDN_Lier_les_cartes.log")

def log(message, level="INFO"):
    """
    Logs a message to the addon's log file and console.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_message_file = f"[{timestamp}] [{level}] {message}\n"
    formatted_message_console = f"[EDN_Lier_les_cartes] [{timestamp}] [{level}] {message}"
    
    # Print to console (so it appears in Anki launch console)
    print(formatted_message_console, file=sys.stdout)
    sys.stdout.flush()
    
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted_message_file)
    except Exception as e:
        print(f"Error writing to log: {e}", file=sys.stderr)

def log_error(message):
    log(message, level="ERROR")

def perf_log(func):
    """Decorator to measure and log function execution time."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        duration = end_time - start_time
        log(f"PERFORMANCE: {func.__module__}.{func.__qualname__} pris {duration:.4f}s")
        return result
    return wrapper
