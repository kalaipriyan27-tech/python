import base64
import ctypes
import speech_recognition as sr
import pyautogui
import datetime
import json
import os
import subprocess
import time
import re
import pyperclip
from ctypes import wintypes
from pathlib import Path
from typing import Any
from urllib import error, request
from pywinauto import Desktop
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto.uia_defines import IUIA
from pywinauto.uia_element_info import UIAElementInfo

WRITE_DELAY = 0.03
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OPENROUTER_MODEL = "openrouter/free"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MAX_TOKENS = 600
OPENROUTER_RESPONSE_FILE = SCRIPT_DIR / "openrouter_response.txt"
DEFAULT_VOICE_RATE = 1
DEFAULT_VOICE_VOLUME = 100
OPENROUTER_SYSTEM_PROMPT = (
    "you are the Java developer or a trainer who helps the students learning "
    "the Java programming.Here your role is to just provide the code that "
    "student ask with statement.Student will give the statement and you "
    "should answer with the only code that can be executed directly in ide "
    "without importing packages or class."
)
API_KEY_ENV_NAMES = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_KEY",
    "OPENROUTER_APIKEY",
    "OPENAI_API_KEY",
)


def voice_feedback_enabled():
    value = os.getenv("SCREENSHOT_VOICE_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def get_voice_rate():
    raw_value = os.getenv("SCREENSHOT_VOICE_RATE")
    if not raw_value:
        return DEFAULT_VOICE_RATE

    try:
        return max(-10, min(10, int(raw_value)))
    except ValueError:
        return DEFAULT_VOICE_RATE


def get_voice_volume():
    raw_value = os.getenv("SCREENSHOT_VOICE_VOLUME")
    if not raw_value:
        return DEFAULT_VOICE_VOLUME

    try:
        return max(0, min(100, int(raw_value)))
    except ValueError:
        return DEFAULT_VOICE_VOLUME


def speak_status(message):
    if not voice_feedback_enabled() or not message:
        return

    script = (
        "Add-Type -AssemblyName System.Speech\n"
        "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$femaleVoice = $voice.GetInstalledVoices() | "
        "ForEach-Object { $_.VoiceInfo } | "
        "Where-Object { $_.Gender -eq 'Female' } | "
        "Select-Object -First 1\n"
        "if ($femaleVoice) { $voice.SelectVoice($femaleVoice.Name) }\n"
        f"$voice.Rate = {get_voice_rate()}\n"
        f"$voice.Volume = {get_voice_volume()}\n"
        "$voice.Speak($env:SCREENSHOT_SPEAK_TEXT)\n"
    )
    encoded_script = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    env = os.environ.copy()
    env["SCREENSHOT_SPEAK_TEXT"] = message

    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-EncodedCommand", encoded_script],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def announce_status(print_message, spoken_message=None):
    print(print_message)
    speak_status(spoken_message or print_message)


def normalize_text(text):
    return " ".join(text.lower().strip().split())


def extract_words(text):
    return re.findall(r"[a-z]+", text.lower())


def detect_command(text):
    words = extract_words(text)
    word_set = set(words)

    if "stop" in word_set:
        return "stop"
    if "hello" in word_set:
        return "shot"
    if "liar" in word_set or "go" in word_set:
        return "go"
    if "get" in word_set:
        return "get"
    if "key" in word_set:
        return "key"
    if "write" in word_set:
        return "write"
    if "copy" in word_set:
        return "copy"

    return None


def get_active_window():
    try:
        return Desktop(backend="uia").window(active_only=True).wrapper_object()
    except Exception as error:
        print("Couldn't get the active window:", error)
        return None


def get_focused_element():
    try:
        element = IUIA().get_focused_element()
        if element is None:
            return None
        return UIAWrapper(UIAElementInfo(element))
    except Exception as error:
        print("Couldn't get the focused element:", error)
        return None


def read_text_pattern_selection(element):
    try:
        selection = element.iface_text.GetSelection()
        selected_text = selection.GetElement(0).GetText(-1).strip()
        if selected_text:
            return selected_text
    except Exception:
        pass

    return ""


def read_selected_text_from_uia(element):
    to_visit = [element]
    visited = set()
    index = 0

    while index < len(to_visit):
        current = to_visit[index]
        index += 1

        try:
            runtime_id = current.element_info.runtime_id
        except Exception:
            runtime_id = id(current)

        runtime_id = tuple(runtime_id) if runtime_id else runtime_id
        if runtime_id in visited:
            continue
        visited.add(runtime_id)

        selected_text = read_text_pattern_selection(current)
        if selected_text:
            return selected_text

        try:
            parent = current.parent()
            if parent is not None:
                to_visit.append(parent)
        except Exception:
            pass

        try:
            for child in current.children():
                to_visit.append(child)
        except Exception:
            pass

    return ""


def get_selected_text(window):
    focused_element = get_focused_element()
    if focused_element is not None:
        try:
            if focused_element.top_level_parent() == window.top_level_parent():
                selected_text = read_selected_text_from_uia(focused_element)
                if selected_text:
                    return selected_text
        except Exception:
            pass

    return ""


def strip_wrapping_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = strip_wrapping_quotes(value.strip())
        if not key:
            continue
        if key in API_KEY_ENV_NAMES:
            os.environ[key] = value
            continue
        if key not in os.environ:
            os.environ[key] = value


def read_env_value(path, target_key):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key.strip() == target_key:
            return strip_wrapping_quotes(value.strip())

    return None


def bootstrap_environment(base_directory):
    for name in (".env", ".env.local"):
        env_path = base_directory / name
        if env_path.exists() and env_path.is_file():
            load_env_file(env_path)

    for alias in API_KEY_ENV_NAMES:
        value = os.getenv(alias)
        if value:
            os.environ["OPENROUTER_API_KEY"] = value
            return


def get_openrouter_api_key():
    for name in API_KEY_ENV_NAMES:
        value = os.getenv(name)
        if value:
            return value
    return None


def print_openrouter_key_debug():
    env_path = SCRIPT_DIR / ".env"
    env_local_path = SCRIPT_DIR / ".env.local"
    env_key = read_env_value(env_path, "OPENROUTER_API_KEY")
    env_local_key = read_env_value(env_local_path, "OPENROUTER_API_KEY")
    effective_key = get_openrouter_api_key()

    print(".env OPENROUTER_API_KEY:", env_key if env_key else "<missing>")
    print(".env.local OPENROUTER_API_KEY:", env_local_key if env_local_key else "<missing>")
    print("Effective OpenRouter key in process:", effective_key if effective_key else "<missing>")
    print("OpenRouter base URL:", get_openrouter_base_url())
    print("OpenRouter model:", get_openrouter_model())

    if env_key and effective_key:
        print("Process key matches .env:", env_key == effective_key)
    elif env_key:
        print("Process key matches .env: False")
    else:
        print("Process key matches .env: <unknown>")


def get_openrouter_model():
    return os.getenv("OPENROUTER_TEXT_MODEL", DEFAULT_OPENROUTER_MODEL)


def get_openrouter_base_url():
    return os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL).rstrip("/")


def get_openrouter_max_tokens():
    raw_value = os.getenv("OPENROUTER_MAX_OUTPUT_TOKENS")
    if not raw_value:
        return DEFAULT_OPENROUTER_MAX_TOKENS

    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_OPENROUTER_MAX_TOKENS


def get_clipboard_text():
    time.sleep(0.2)

    CF_UNICODETEXT = 13
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL

    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    for _ in range(3):
        try:
            if user32.IsClipboardFormatAvailable(CF_UNICODETEXT) == 0:
                time.sleep(0.1)
                continue

            if user32.OpenClipboard(None) == 0:
                time.sleep(0.1)
                continue

            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if handle:
                    size = kernel32.GlobalSize(handle)
                    if size > 0:
                        locked_memory = kernel32.GlobalLock(handle)
                        if locked_memory:
                            try:
                                text = ctypes.wstring_at(locked_memory)
                                return text
                            finally:
                                kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        except Exception:
            pass

        time.sleep(0.1)

    try:
        return pyperclip.paste()
    except Exception:
        return ""


def extract_output_text(payload: dict[str, Any]):
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    message = choices[0].get("message", {})
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    if isinstance(content, str):
        return content.strip()

    if not isinstance(content, list):
        return ""

    chunks = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())

    return "\n".join(chunks).strip()


def summarize_empty_response(payload: dict[str, Any]):
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return "response had no choices"

    choice = choices[0]
    if not isinstance(choice, dict):
        return "first choice was not an object"

    finish_reason = choice.get("finish_reason")
    message = choice.get("message")
    if not isinstance(message, dict):
        return f"finish_reason={finish_reason!r}, message was missing"

    refusal = message.get("refusal")
    content = message.get("content")
    return (
        f"finish_reason={finish_reason!r}, refusal={refusal!r}, "
        f"content_type={type(content).__name__}"
    )


def send_openrouter_text_request(prompt_text):
    api_key = get_openrouter_api_key()
    print("Go executed")
    if not api_key:
        raise RuntimeError(
            "OpenRouter key not found. Set OPENROUTER_API_KEY or put it in .env / .env.local."
        )

    payload = {
        "model": get_openrouter_model(),
        "messages": [
            {
                "role": "system",
                "content": OPENROUTER_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": prompt_text,
            }
        ],
        "max_tokens": get_openrouter_max_tokens(),
    }

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    site_url = os.getenv("OPENROUTER_SITE_URL")
    site_name = os.getenv("OPENROUTER_APP_NAME")
    if site_url:
        headers["HTTP-Referer"] = site_url
    if site_name:
        headers["X-OpenRouter-Title"] = site_name

    api_request = request.Request(
        f"{get_openrouter_base_url()}/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with request.urlopen(api_request, timeout=120) as response:
            response_text = response.read().decode("utf-8")
    except error.HTTPError as exc:
        response_text = exc.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(response_text)
            message = details.get("error", {}).get("message", response_text)
        except json.JSONDecodeError:
            message = response_text
        if exc.code == 401 and "User not found" in message:
            raise RuntimeError(
                "OpenRouter rejected the API key (401 User not found). "
                "Update OPENROUTER_API_KEY in .env with a valid OpenRouter key and restart the script."
            ) from exc
        raise RuntimeError(f"OpenRouter API error {exc.code}: {message}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach the OpenRouter API: {exc.reason}") from exc

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenRouter API returned invalid JSON.") from exc


def save_openrouter_response_text(text):
    OPENROUTER_RESPONSE_FILE.write_text(text, encoding="utf-8")


def load_openrouter_response_text():
    try:
        return OPENROUTER_RESPONSE_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def process_clipboard_with_openrouter():
    clipboard_text = get_clipboard_text()
    if not clipboard_text:
        announce_status("Clipboard is empty.", "Clipboard is empty.")
        return

    announce_status(
        "Sending clipboard text to OpenRouter...",
        "Sending clipboard text to the language model.",
    )
    response_payload = send_openrouter_text_request(clipboard_text)
    response_text = extract_output_text(response_payload)
    if not response_text:
        summary = summarize_empty_response(response_payload)
        raise RuntimeError(f"OpenRouter response did not contain text: {summary}")

    save_openrouter_response_text(response_text)
    announce_status(
        f"OpenRouter response saved to: {OPENROUTER_RESPONSE_FILE}",
        "Language model response saved to file.",
    )


def type_saved_openrouter_response():
    stored_text = load_openrouter_response_text()
    if not stored_text:
        announce_status("No saved OpenRouter response found.", "No saved response found.")
        return

    write_clipboard_text(stored_text, spoken_label="saved response")


def type_text_like_human(text, delay=WRITE_DELAY):
    if not text:
        announce_status("Clipboard is empty.", "Clipboard is empty.")
        return

    announce_status("Typing clipboard text...", "Typing clipboard text.")

    index = 0
    while index < len(text):
        char = text[index]

        if char == "\r":
            if index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            pyautogui.press("enter")
        elif char == "\n":
            pyautogui.press("enter")
        elif char == "\t":
            pyautogui.press("tab")
        elif char == " ":
            pyautogui.press("space")
        else:
            pyautogui.write(char, interval=delay)

        time.sleep(delay)

        index += 1

    announce_status("Finished typing clipboard text.", "Finished typing clipboard text.")


def type_line_like_human(text, delay=WRITE_DELAY):
    for char in text:
        if char == "\t":
            pyautogui.press("tab")
        elif char == " ":
            pyautogui.press("space")
        else:
            pyautogui.write(char, interval=delay)

        time.sleep(delay)

    # Editors like VS Code may auto-insert a matching "}" when a block line
    # ends with "{". Remove that generated closer so only the clipboard text remains.
    if text.rstrip().endswith("{"):
        pyautogui.press("delete")
        time.sleep(delay)


def clear_auto_indent(delay=WRITE_DELAY):
    # After Enter, many editors place the caret after auto-inserted indent.
    # Select that indent so the next line can be typed exactly as stored.
    pyautogui.hotkey("shift", "home")
    time.sleep(delay)
    pyautogui.press("delete")
    time.sleep(delay)


def write_clipboard_text(text, delay=WRITE_DELAY, spoken_label="clipboard text"):
    if not text:
        announce_status("Clipboard is empty.", "Clipboard is empty.")
        return

    announce_status(
        f"Typing {spoken_label} exactly...",
        f"Typing {spoken_label}.",
    )

    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized_text.split("\n")

    type_line_like_human(lines[0], delay=delay)

    for line in lines[1:]:
        pyautogui.press("enter")
        time.sleep(delay)
        clear_auto_indent(delay=delay)
        type_line_like_human(line, delay=delay)

    announce_status(
        f"Finished typing {spoken_label}.",
        f"Finished typing {spoken_label}.",
    )


def save_screenshot():
    filename = "screenshot_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
    screenshot = pyautogui.screenshot()
    screenshot.save(filename)
    announce_status(f"Screenshot saved: {filename}", "Screenshot saved.")


def main():
    bootstrap_environment(SCRIPT_DIR)

    recognizer = sr.Recognizer()
    mic = sr.Microphone()

    announce_status(
        "Listening for the words 'hello', 'copy', 'write', 'liar', 'get', 'key', and 'stop'...",
        "Voice assistant is ready.",
    )

    while True:
        with mic as source:
            recognizer.adjust_for_ambient_noise(source)
            audio = recognizer.listen(source)

        try:
            text = recognizer.recognize_google(audio)
            print(f"DEBUG - Raw speech: '{text}'")
            normalized_text = normalize_text(text)
            command = detect_command(normalized_text)
            print("You said:", normalized_text)

            if command == "stop":
                announce_status("Stopping.", "Stopping.")
                break

            if command == "shot":
                try:
                    save_screenshot()
                except pyautogui.FailSafeException:
                    announce_status("PyAutoGUI safety stop triggered.", "Safety stop triggered.")
                    print("Move your mouse away from the screen corners and try again.")

            if command == "copy":
                window = get_active_window()
                if window is None:
                    continue

                selected_text = get_selected_text(window)
                if selected_text:
                    print("Selected text:", selected_text)
                    try:
                        pyperclip.copy(selected_text)
                        announce_status("Copied to clipboard!", "Selected text copied to clipboard.")
                    except Exception:
                        announce_status("Failed to copy to clipboard", "Failed to copy to clipboard.")
                else:
                    announce_status(
                        f"No accessible selected text was found in: {window.window_text()}",
                        "No selected text was found.",
                    )
                    print("Clipboard fallback is disabled to avoid triggering webpage copy events.")

            if command == "write":
                clipboard_text = get_clipboard_text()
                print(f"DEBUG - Clipboard LEN: {len(clipboard_text)}, repr: {repr(clipboard_text)}")
                print(f"DEBUG - Clipboard: '{clipboard_text[:50]}...'" if len(clipboard_text) > 50 else f"DEBUG - Clipboard: '{clipboard_text}'")
                try:
                    write_clipboard_text(clipboard_text, spoken_label="clipboard text")
                except pyautogui.FailSafeException:
                    announce_status("PyAutoGUI safety stop triggered.", "Safety stop triggered.")
                    print("Move your mouse away from the screen corners and try again.")

            if command == "go":
                try:
                    process_clipboard_with_openrouter()
                except RuntimeError as error_message:
                    print(error_message)

            if command == "get":
                try:
                    type_saved_openrouter_response()
                except pyautogui.FailSafeException:
                    announce_status("PyAutoGUI safety stop triggered.", "Safety stop triggered.")
                    print("Move your mouse away from the screen corners and try again.")

            if command == "key":
                print_openrouter_key_debug()

            if command is None:
                print("No command detected.")

        except sr.UnknownValueError:
            print("Couldn't understand audio")

        except sr.RequestError:
            print("Speech service error")


if __name__ == "__main__":
    main()
