import argparse
import ctypes
import random
import time
from ctypes import wintypes


DEFAULT_TEXT = "hello_ world"
DEFAULT_MIN_KEY_DELAY = 0.045
DEFAULT_MAX_KEY_DELAY = 0.14
DEFAULT_MIN_KEY_HOLD = 0.02
DEFAULT_MAX_KEY_HOLD = 0.065
DEFAULT_PAUSE_CHANCE = 0.14
DEFAULT_PAUSE_MIN = 0.18
DEFAULT_PAUSE_MAX = 0.5
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
ULONG_PTR = wintypes.WPARAM

user32 = ctypes.WinDLL("user32", use_last_error=True)


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Click a target position and type text there.'
    )
    parser.add_argument(
        "--x",
        type=int,
        help="Screen X coordinate. If omitted, the current mouse X is used after the delay.",
    )
    parser.add_argument(
        "--y",
        type=int,
        help="Screen Y coordinate. If omitted, the current mouse Y is used after the delay.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds to wait before reading the current mouse position.",
    )
    parser.add_argument(
        "--text",
        default=DEFAULT_TEXT,
        help=f'Text to type. Default: "{DEFAULT_TEXT}"',
    )
    parser.add_argument(
        "--click-delay",
        type=float,
        default=0.15,
        help="Seconds to wait after clicking before typing.",
    )
    parser.add_argument(
        "--min-key-delay",
        type=float,
        default=DEFAULT_MIN_KEY_DELAY,
        help="Minimum delay between characters in seconds.",
    )
    parser.add_argument(
        "--max-key-delay",
        type=float,
        default=DEFAULT_MAX_KEY_DELAY,
        help="Maximum delay between characters in seconds.",
    )
    parser.add_argument(
        "--min-key-hold",
        type=float,
        default=DEFAULT_MIN_KEY_HOLD,
        help="Minimum time each key stays pressed in seconds.",
    )
    parser.add_argument(
        "--max-key-hold",
        type=float,
        default=DEFAULT_MAX_KEY_HOLD,
        help="Maximum time each key stays pressed in seconds.",
    )
    parser.add_argument(
        "--pause-chance",
        type=float,
        default=DEFAULT_PAUSE_CHANCE,
        help="Chance of adding a short thinking pause after a character, from 0 to 1.",
    )
    parser.add_argument(
        "--pause-min",
        type=float,
        default=DEFAULT_PAUSE_MIN,
        help="Minimum duration of an occasional thinking pause in seconds.",
    )
    parser.add_argument(
        "--pause-max",
        type=float,
        default=DEFAULT_PAUSE_MAX,
        help="Maximum duration of an occasional thinking pause in seconds.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Optional random seed for repeatable timing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the target position and text without clicking or typing.",
    )
    return parser.parse_args()


def get_cursor_position() -> tuple[int, int]:
    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    return point.x, point.y


def resolve_target_position(args: argparse.Namespace) -> tuple[int, int]:
    if args.x is not None or args.y is not None:
        if args.x is None or args.y is None:
            raise ValueError("Both --x and --y must be provided together.")
        return args.x, args.y

    print(f"Move the mouse to the target position within {args.delay:.1f} seconds...")
    time.sleep(args.delay)
    return get_cursor_position()


def validate_args(args: argparse.Namespace) -> None:
    if args.min_key_delay < 0 or args.max_key_delay < 0:
        raise ValueError("Key delays must be non-negative.")
    if args.min_key_hold < 0 or args.max_key_hold < 0:
        raise ValueError("Key hold times must be non-negative.")
    if args.pause_min < 0 or args.pause_max < 0:
        raise ValueError("Pause times must be non-negative.")
    if args.min_key_delay > args.max_key_delay:
        raise ValueError("--min-key-delay must be less than or equal to --max-key-delay.")
    if args.min_key_hold > args.max_key_hold:
        raise ValueError("--min-key-hold must be less than or equal to --max-key-hold.")
    if args.pause_min > args.pause_max:
        raise ValueError("--pause-min must be less than or equal to --pause-max.")
    if not 0 <= args.pause_chance <= 1:
        raise ValueError("--pause-chance must be between 0 and 1.")


def send_input(*inputs: INPUT) -> None:
    if not inputs:
        return

    buffer = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(buffer), buffer, ctypes.sizeof(INPUT))
    if sent != len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())


def make_mouse_input(flags: int) -> INPUT:
    return INPUT(
        type=INPUT_MOUSE,
        union=INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=0,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )


def make_keyboard_input(char: str, key_up: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE
    if key_up:
        flags |= KEYEVENTF_KEYUP

    return INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(
            ki=KEYBDINPUT(
                wVk=0,
                wScan=ord(char),
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )


def click_at(x: int, y: int) -> None:
    if not user32.SetCursorPos(x, y):
        raise ctypes.WinError(ctypes.get_last_error())
    send_input(
        make_mouse_input(MOUSEEVENTF_LEFTDOWN),
        make_mouse_input(MOUSEEVENTF_LEFTUP),
    )


def choose_inter_key_delay(
    rng: random.Random,
    char: str,
    previous_char: str | None,
    args: argparse.Namespace,
) -> float:
    delay = rng.uniform(args.min_key_delay, args.max_key_delay)

    if char == " ":
        delay += rng.uniform(0.03, 0.12)
    elif char in ",.;:!?":
        delay += rng.uniform(0.08, 0.22)
    elif char in "_-/\\":
        delay += rng.uniform(0.04, 0.12)

    if previous_char == char:
        delay += rng.uniform(0.02, 0.06)

    return delay


def maybe_pause(
    rng: random.Random,
    char: str,
    args: argparse.Namespace,
) -> float:
    pause_chance = args.pause_chance
    if char in ",.;:!?":
        pause_chance = min(1.0, pause_chance + 0.2)
    elif char == " ":
        pause_chance = min(1.0, pause_chance + 0.08)

    if rng.random() < pause_chance:
        return rng.uniform(args.pause_min, args.pause_max)
    return 0.0


def type_text(text: str, args: argparse.Namespace, rng: random.Random) -> None:
    previous_char = None
    for index, char in enumerate(text):
        key_hold = rng.uniform(args.min_key_hold, args.max_key_hold)
        send_input(make_keyboard_input(char, key_up=False))
        time.sleep(key_hold)
        send_input(make_keyboard_input(char, key_up=True))

        if index == len(text) - 1:
            previous_char = char
            continue

        time.sleep(choose_inter_key_delay(rng, char, previous_char, args))
        extra_pause = maybe_pause(rng, char, args)
        if extra_pause:
            time.sleep(extra_pause)
        previous_char = char


def main() -> None:
    args = parse_args()
    validate_args(args)
    rng = random.Random(args.seed)
    x, y = resolve_target_position(args)
    print(f"Target position: ({x}, {y})")
    print(f'Text: "{args.text}"')
    print(
        "Typing profile: "
        f"delay {args.min_key_delay:.3f}-{args.max_key_delay:.3f}s, "
        f"hold {args.min_key_hold:.3f}-{args.max_key_hold:.3f}s, "
        f"pause chance {args.pause_chance:.2f}"
    )

    if args.dry_run:
        print("Dry run complete. No click or typing performed.")
        return

    click_at(x, y)
    time.sleep(args.click_delay)
    type_text(args.text, args, rng)


if __name__ == "__main__":
    main()
