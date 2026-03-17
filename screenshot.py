import speech_recognition as sr
import pyautogui
import datetime
import time
import re
import pyperclip
from pywinauto import Desktop
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto.uia_defines import IUIA
from pywinauto.uia_element_info import UIAElementInfo

WRITE_DELAY = 0.05


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


def get_clipboard_text():
    try:
        return pyperclip.paste()
    except pyperclip.PyperclipException as error:
        print("Couldn't read the clipboard:", error)
        return ""


def type_text_like_human(text, delay=WRITE_DELAY):
    if not text:
        print("Clipboard is empty.")
        return

    print("Typing clipboard text...")

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
        else:
            pyautogui.write(char, interval=delay)

        if char in "\r\n\t":
            time.sleep(delay)

        index += 1

    print("Finished typing clipboard text.")


def save_screenshot():
    filename = "screenshot_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
    screenshot = pyautogui.screenshot()
    screenshot.save(filename)
    print("Screenshot saved:", filename)


recognizer = sr.Recognizer()
mic = sr.Microphone()

print("Listening for the words 'hello', 'do', 'write', and 'stop'...")

while True:
    with mic as source:
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)

    try:
        text = recognizer.recognize_google(audio)
        normalized_text = normalize_text(text)
        command = detect_command(normalized_text)
        print("You said:", normalized_text)

        if command == "stop":
            print("Stopping.")
            break

        if command == "shot":
            try:
                save_screenshot()
            except pyautogui.FailSafeException:
                print("PyAutoGUI safety stop triggered.")
                print("Move your mouse away from the screen corners and try again.")

        if command == "copy":
            window = get_active_window()
            if window is None:
                continue

            selected_text = get_selected_text(window)
            if selected_text:
                print("Selected text:", selected_text)
            else:
                print("No accessible selected text was found in:", window.window_text())
                print("Clipboard fallback is disabled to avoid triggering webpage copy events.")

        if command == "write":
            clipboard_text = get_clipboard_text()
            try:
                type_text_like_human(clipboard_text)
            except pyautogui.FailSafeException:
                print("PyAutoGUI safety stop triggered.")
                print("Move your mouse away from the screen corners and try again.")

        if command is None:
            print("No command detected.")

    except sr.UnknownValueError:
        print("Couldn't understand audio")

    except sr.RequestError:
        print("Speech service error")
