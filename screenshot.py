import speech_recognition as sr
import pyautogui
import datetime
recognizer = sr.Recognizer()
mic = sr.Microphone()

print("Listening for the word 'hello'...")

while True:
    with mic as source:
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)

    try:
        text = recognizer.recognize_google(audio).lower()
        print("You said:", text)

        if "hello" in text:
            filename = "screenshot_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
            screenshot = pyautogui.screenshot()
            screenshot.save(filename)
            print("Screenshot saved:", filename)

    except sr.UnknownValueError:
        print("Couldn't understand audio")

    except sr.RequestError:
        print("Speech service error")