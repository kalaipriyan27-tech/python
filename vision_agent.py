from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

import pyautogui


DEFAULT_MODEL = "openrouter/free"
FALLBACK_MODELS = (
    "openrouter/free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
)
API_KEY_ENV_NAMES = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_KEY",
    "OPENROUTER_APIKEY",
    "OPENAI_API_KEY",
)
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
MAX_IMAGE_EDGE = 1600
ABORT_KEY = "f10"
ABORT_KEY_VK = 0x79


SYSTEM_PROMPT = """You are a careful vision-only desktop agent.
You receive a screenshot, the user's goal, screen size, cursor position, step number, and the last action result.
Return exactly one JSON object with no markdown, no commentary, and no extra text.

Allowed actions:
- move: move the cursor to x,y
- click: click at x,y with an optional button and click count
- type: type the provided text
- press: press one keyboard key
- scroll: scroll by an integer amount
- wait: wait for a short duration
- done: the goal is complete

Rules:
- Use absolute screen coordinates.
- Be conservative. If uncertain, prefer move or wait over click.
- Only choose actions visible and justified by the screenshot.
- If the task appears complete, return done.
- Never return more than one action.

JSON schema:
{
  "action": "move|click|type|press|scroll|wait|done",
  "x": 0,
  "y": 0,
  "button": "left|right|middle",
  "clicks": 1,
  "text": "",
  "key": "",
  "amount": 0,
  "seconds": 1.0,
  "reason": ""
}
"""


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: Path) -> None:
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
        if key and key not in os.environ:
            os.environ[key] = value


def bootstrap_environment(base_directory: Path) -> None:
    for name in (".env", ".env.local"):
        env_path = base_directory / name
        if env_path.exists() and env_path.is_file():
            load_env_file(env_path)

    for alias in API_KEY_ENV_NAMES:
        value = os.getenv(alias)
        if value:
            os.environ["OPENROUTER_API_KEY"] = value
            return


def get_openrouter_api_key() -> str | None:
    for name in API_KEY_ENV_NAMES:
        value = os.getenv(name)
        if value:
            return value
    return None


def build_model_attempts(primary_model: str) -> list[str]:
    attempts: list[str] = []
    for model_name in (primary_model, *FALLBACK_MODELS):
        if model_name and model_name not in attempts:
            attempts.append(model_name)
    return attempts


def extract_output_text(payload: dict[str, Any]) -> str:
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

    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    return "\n".join(chunks).strip()


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError("Model response did not contain a JSON object.")
        return json.loads(text[start : end + 1])


def make_data_url(image_bytes: bytes, mime_type: str = "image/png") -> str:
    base64_image = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{base64_image}"


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def abort_requested() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetAsyncKeyState(ABORT_KEY_VK) & 0x8000)
    except Exception:
        return False


def capture_screenshot(step_path: Path) -> tuple[bytes, tuple[int, int]]:
    image = pyautogui.screenshot()
    original_size = image.size
    image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    step_path.write_bytes(image_bytes)
    return image_bytes, original_size


def request_next_action(
    *,
    api_key: str,
    base_url: str,
    model: str,
    detail: str,
    image_bytes: bytes,
    goal: str,
    screen_size: tuple[int, int],
    cursor_position: tuple[int, int],
    step_index: int,
    last_result: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    prompt = (
        f"Goal: {goal}\n"
        f"Step: {step_index}\n"
        f"Screen size: {screen_size[0]}x{screen_size[1]}\n"
        f"Cursor position: {cursor_position[0]},{cursor_position[1]}\n"
        f"Last action result: {last_result}\n"
        f"If nothing should happen yet, use a short wait action.\n"
        f"If the goal is complete, return done."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": SYSTEM_PROMPT}]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": make_data_url(image_bytes),
                            "detail": detail,
                        },
                    },
                ],
            },
        ],
        "max_tokens": 250,
        "temperature": 0,
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
        f"{base_url}/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with request.urlopen(api_request, timeout=180) as response:
            response_text = response.read().decode("utf-8")
    except error.HTTPError as exc:
        response_text = exc.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(response_text)
            message = details.get("error", {}).get("message", response_text)
        except json.JSONDecodeError:
            message = response_text
        raise RuntimeError(f"OpenRouter API error {exc.code}: {message}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach the OpenRouter API: {exc.reason}") from exc

    try:
        response_payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenRouter API returned invalid JSON.") from exc

    model_text = extract_output_text(response_payload)
    if not model_text:
        raise RuntimeError("OpenRouter returned no action text.")

    action = extract_json_object(model_text)
    return action, response_payload, model_text


def coerce_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def coerce_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def execute_action(
    action: dict[str, Any],
    *,
    move_duration: float,
    type_interval: float,
) -> str:
    action_name = str(action.get("action", "")).lower()

    if action_name == "move":
        x = coerce_int(action.get("x"))
        y = coerce_int(action.get("y"))
        pyautogui.moveTo(x, y, duration=move_duration)
        return f"Moved cursor to ({x}, {y})."

    if action_name == "click":
        x = coerce_int(action.get("x"))
        y = coerce_int(action.get("y"))
        button = str(action.get("button", "left")).lower()
        clicks = max(1, coerce_int(action.get("clicks", 1), 1))
        pyautogui.moveTo(x, y, duration=move_duration)
        pyautogui.click(x=x, y=y, button=button, clicks=clicks)
        return f"Clicked {button} at ({x}, {y}) with {clicks} click(s)."

    if action_name == "type":
        text = str(action.get("text", ""))
        pyautogui.write(text, interval=type_interval)
        return f"Typed {len(text)} characters."

    if action_name == "press":
        key = str(action.get("key", "")).lower()
        if not key:
            raise RuntimeError("Press action was missing a key.")
        pyautogui.press(key)
        return f"Pressed key '{key}'."

    if action_name == "scroll":
        amount = coerce_int(action.get("amount"))
        pyautogui.scroll(amount)
        return f"Scrolled by {amount}."

    if action_name == "wait":
        seconds = max(0.1, coerce_float(action.get("seconds", 1.0), 1.0))
        time.sleep(seconds)
        return f"Waited {seconds:.2f} seconds."

    if action_name == "done":
        return "Goal marked complete."

    raise RuntimeError(f"Unsupported action: {action_name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a vision-only local desktop agent against the visible screen."
    )
    parser.add_argument("--goal", required=True, help="The task goal for the agent.")
    parser.add_argument(
        "--steps",
        type=int,
        default=1,
        help="Maximum number of agent steps to run. Defaults to 1.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute actions for real. Without this flag, the agent only proposes actions.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENROUTER_VISION_MODEL", DEFAULT_MODEL),
        help=f"OpenRouter model to use. Defaults to {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--detail",
        choices=("auto", "low", "high", "original"),
        default=os.getenv("OPENROUTER_VISION_DETAIL", "high"),
        help="Image detail level for the model. Defaults to high.",
    )
    parser.add_argument(
        "--start-delay",
        type=float,
        default=3.0,
        help="Seconds to wait before the first screenshot so you can focus the target window.",
    )
    parser.add_argument(
        "--move-duration",
        type=float,
        default=0.12,
        help="Mouse movement duration in seconds for move and click actions.",
    )
    parser.add_argument(
        "--type-interval",
        type=float,
        default=0.01,
        help="Delay between typed characters for type actions.",
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    base_dir = Path(__file__).resolve().parent
    bootstrap_environment(base_dir)

    api_key = get_openrouter_api_key()
    if not api_key:
        print("OpenRouter key not found in .env, .env.local, or known environment variables.", file=sys.stderr)
        return 1

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0
    if hasattr(pyautogui, "MINIMUM_DURATION"):
        pyautogui.MINIMUM_DURATION = 0
    if hasattr(pyautogui, "MINIMUM_SLEEP"):
        pyautogui.MINIMUM_SLEEP = 0

    if args.steps < 1:
        print("--steps must be at least 1.", file=sys.stderr)
        return 1

    if not args.execute and args.steps > 1:
        print("Dry-run mode with more than one step may repeat similar actions because the screen does not change.")

    debug_root = base_dir.parent / "automation-debug"
    debug_dir = debug_root / f"vision-agent-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    debug_dir.mkdir(parents=True, exist_ok=True)

    print(f"Goal: {args.goal}")
    print(f"Model: {args.model}")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY-RUN'}")
    print(f"Abort key: {ABORT_KEY.upper()}")
    print(f"Debug dir: {debug_dir}")

    if args.start_delay > 0:
        print(f"Focus the target window. Starting in {args.start_delay:.1f}s...")
        time.sleep(args.start_delay)

    base_url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    last_result = "No previous action."

    for step_index in range(1, args.steps + 1):
        if abort_requested():
            print(f"[{iso_now()}] Abort key pressed before step {step_index}.")
            return 1

        screenshot_path = debug_dir / f"step_{step_index:03d}.png"
        image_bytes, screen_size = capture_screenshot(screenshot_path)
        cursor_position = pyautogui.position()

        action = None
        raw_payload = None
        raw_text = None
        last_error: str | None = None

        for candidate_model in build_model_attempts(args.model):
            try:
                action, raw_payload, raw_text = request_next_action(
                    api_key=api_key,
                    base_url=base_url,
                    model=candidate_model,
                    detail=args.detail,
                    image_bytes=image_bytes,
                    goal=args.goal,
                    screen_size=screen_size,
                    cursor_position=cursor_position,
                    step_index=step_index,
                    last_result=last_result,
                )
                action["_used_model"] = candidate_model
                break
            except RuntimeError as exc:
                last_error = str(exc)
                print(f"[{iso_now()}] {candidate_model} failed for step {step_index}: {last_error}")

        if action is None or raw_payload is None or raw_text is None:
            print(f"Agent planning failed on step {step_index}: {last_error}", file=sys.stderr)
            return 1

        save_json(debug_dir / f"step_{step_index:03d}_response.json", raw_payload)
        save_json(debug_dir / f"step_{step_index:03d}_action.json", action)
        (debug_dir / f"step_{step_index:03d}_action.txt").write_text(raw_text, encoding="utf-8")

        action_name = str(action.get("action", "")).lower()
        reason = str(action.get("reason", "")).strip()
        used_model = str(action.get("_used_model", args.model))
        print(f"[{iso_now()}] Step {step_index} -> {action_name} via {used_model}")
        if reason:
            print(f"Reason: {reason}")
        print(json.dumps(action, indent=2))

        if action_name == "done":
            print("Agent marked the goal complete.")
            return 0

        if not args.execute:
            print("Dry-run mode: action not executed.")
            last_result = f"Dry-run only. Proposed action: {action_name}."
            continue

        if abort_requested():
            print(f"[{iso_now()}] Abort key pressed before executing step {step_index}.")
            return 1

        try:
            last_result = execute_action(
                action,
                move_duration=args.move_duration,
                type_interval=args.type_interval,
            )
        except RuntimeError as exc:
            print(f"Execution failed on step {step_index}: {exc}", file=sys.stderr)
            return 1

        print(f"Result: {last_result}")
        time.sleep(0.4)

    print("Reached the maximum step limit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
