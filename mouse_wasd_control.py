import ctypes
import math
import time
import tkinter as tk

import pyautogui


DEFAULT_MAX_SPEED = 760.0
MIN_MAX_SPEED = 280.0
MAX_MAX_SPEED = 1600.0
SPEED_ADJUST_STEP = 80.0
FAST_MULTIPLIER = 1.7
PRECISION_MULTIPLIER = 0.35
ULTRA_PRECISION_MULTIPLIER = 0.18
ACCEL_RESPONSE = 30.0
DECEL_RESPONSE = 65.0
TURN_RESPONSE = 60.0
STOP_THRESHOLD = 24.0
TICK_MS = 8
VK_CODES = {
    "w": ord("W"),
    "a": ord("A"),
    "s": ord("S"),
    "d": ord("D"),
    "r": ord("R"),
    "shift": 0x10,
    "control": 0x11,
    "alt": 0x12,
    "space": 0x20,
    "escape": 0x1B,
    "bracketleft": 0xDB,
    "bracketright": 0xDD,
    "f8": 0x77,
    "f9": 0x78,
}
MOVEMENT_KEYS = {"w", "a", "s", "d", "shift", "control", "alt"}
WINDOWS_USER32 = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def click_at(x: int, y: int, button: str = "left") -> None:
    pyautogui.click(x=x, y=y, button=button)


def get_cursor_position() -> tuple[int, int]:
    point = POINT()
    WINDOWS_USER32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


class MouseWasdController:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Mouse WASD Control")
        self.root.geometry("400x255")
        self.root.resizable(False, False)

        self.pressed_keys: set[str] = set()
        self.previous_global_keys: set[str] = set()
        self.mouse_mode_enabled = False
        self.max_speed = DEFAULT_MAX_SPEED
        self.velocity_x = 0.0
        self.velocity_y = 0.0
        self.carry_x = 0.0
        self.carry_y = 0.0
        self.last_tick = time.perf_counter()

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0
        if hasattr(pyautogui, "MINIMUM_DURATION"):
            pyautogui.MINIMUM_DURATION = 0
        if hasattr(pyautogui, "MINIMUM_SLEEP"):
            pyautogui.MINIMUM_SLEEP = 0

        instructions = (
            "Press F8 to enable or disable global mouse mode.\n"
            "When enabled, W A S D moves the mouse in any app.\n"
            "Hold Shift to move faster, Ctrl for precision, Alt for ultra precision.\n"
            "Use [ and ] to tune speed, R to reset speed, Space to click.\n"
            "Press Esc to stop movement, or F9 to quit the program."
        )

        label = tk.Label(
            self.root,
            text=instructions,
            justify="left",
            padx=20,
            pady=20,
            font=("Segoe UI", 11),
        )
        label.pack(fill="both", expand=True)

        self.status_var = tk.StringVar()
        self.status_label = tk.Label(
            self.root,
            textvariable=self.status_var,
            anchor="w",
            padx=20,
            pady=0,
            font=("Segoe UI", 10),
        )
        self.status_label.pack(fill="x")

        self.cursor_var = tk.StringVar()
        self.cursor_label = tk.Label(
            self.root,
            textvariable=self.cursor_var,
            anchor="w",
            padx=20,
            pady=10,
            font=("Consolas", 11),
        )
        self.cursor_label.pack(fill="x")
        self.update_status()
        self.update_cursor_status()

        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        self.tick()

    def is_key_down(self, key_name: str) -> bool:
        return bool(WINDOWS_USER32.GetAsyncKeyState(VK_CODES[key_name]) & 0x8000)

    def stop_motion(self) -> None:
        self.pressed_keys.clear()
        self.velocity_x = 0.0
        self.velocity_y = 0.0
        self.carry_x = 0.0
        self.carry_y = 0.0

    def handle_global_input(self) -> None:
        current_keys = {name for name in VK_CODES if self.is_key_down(name)}
        newly_pressed = current_keys - self.previous_global_keys

        if "f8" in newly_pressed:
            self.mouse_mode_enabled = not self.mouse_mode_enabled
            if not self.mouse_mode_enabled:
                self.stop_motion()

        if "f9" in newly_pressed:
            self.root.destroy()
            return

        if "escape" in newly_pressed:
            self.mouse_mode_enabled = False
            self.stop_motion()

        if self.mouse_mode_enabled:
            if "space" in newly_pressed:
                pyautogui.click()

            if "bracketleft" in newly_pressed:
                self.max_speed = max(MIN_MAX_SPEED, self.max_speed - SPEED_ADJUST_STEP)

            if "bracketright" in newly_pressed:
                self.max_speed = min(MAX_MAX_SPEED, self.max_speed + SPEED_ADJUST_STEP)

            if "r" in newly_pressed:
                self.max_speed = DEFAULT_MAX_SPEED

            self.pressed_keys = current_keys & MOVEMENT_KEYS
        else:
            self.stop_motion()

        self.previous_global_keys = current_keys
        self.update_status()

    def update_status(self) -> None:
        mode = "Off"
        if self.mouse_mode_enabled:
            mode = "Normal"
        if "shift" in self.pressed_keys:
            mode = "Fast"
        if "control" in self.pressed_keys:
            mode = "Precision"
        if "alt" in self.pressed_keys:
            mode = "Ultra precision"

        state = "Enabled" if self.mouse_mode_enabled else "Disabled"
        self.status_var.set(f"Mouse mode: {state} | Speed: {int(self.max_speed)} px/s | Mode: {mode}")

    def update_cursor_status(self) -> None:
        x, y = get_cursor_position()
        self.cursor_var.set(f"Current mouse position: x={x:4d}  y={y:4d}")

    def get_direction(self) -> tuple[float, float]:
        dx = float("d" in self.pressed_keys) - float("a" in self.pressed_keys)
        dy = float("s" in self.pressed_keys) - float("w" in self.pressed_keys)

        if dx and dy:
            scale = 1 / math.sqrt(2)
            dx *= scale
            dy *= scale

        return dx, dy

    def tick(self) -> None:
        self.handle_global_input()

        now = time.perf_counter()
        dt = min(now - self.last_tick, 0.05)
        self.last_tick = now

        direction_x, direction_y = self.get_direction()

        max_speed = self.max_speed
        if "shift" in self.pressed_keys:
            max_speed *= FAST_MULTIPLIER
        if "control" in self.pressed_keys:
            max_speed *= PRECISION_MULTIPLIER
        if "alt" in self.pressed_keys:
            max_speed *= ULTRA_PRECISION_MULTIPLIER

        target_velocity_x = direction_x * max_speed
        target_velocity_y = direction_y * max_speed

        response = ACCEL_RESPONSE if direction_x or direction_y else DECEL_RESPONSE
        if target_velocity_x and self.velocity_x and math.copysign(1.0, target_velocity_x) != math.copysign(1.0, self.velocity_x):
            response = max(response, TURN_RESPONSE)
        if target_velocity_y and self.velocity_y and math.copysign(1.0, target_velocity_y) != math.copysign(1.0, self.velocity_y):
            response = max(response, TURN_RESPONSE)

        blend = min(1.0, response * dt)

        self.velocity_x += (target_velocity_x - self.velocity_x) * blend
        self.velocity_y += (target_velocity_y - self.velocity_y) * blend

        if not direction_x and abs(self.velocity_x) < STOP_THRESHOLD:
            self.velocity_x = 0.0
            self.carry_x = 0.0
        if not direction_y and abs(self.velocity_y) < STOP_THRESHOLD:
            self.velocity_y = 0.0
            self.carry_y = 0.0

        self.carry_x += self.velocity_x * dt
        self.carry_y += self.velocity_y * dt

        move_x = math.trunc(self.carry_x)
        move_y = math.trunc(self.carry_y)

        if move_x or move_y:
            self.carry_x -= move_x
            self.carry_y -= move_y
            pyautogui.moveRel(move_x, move_y, duration=0)

        self.update_cursor_status()

        self.root.after(TICK_MS, self.tick)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    MouseWasdController().run()
