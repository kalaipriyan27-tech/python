from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


DEFAULT_PROMPT = (
    "Describe this screenshot in plain English. Explain what app, website, "
    "game, or document it appears to show, what the screen is mainly about, "
    "and any important visible text, warnings, or errors. Keep it concise "
    "and mention uncertainty if text is too small to read clearly."
)

DEFAULT_OPENROUTER_MODEL = "openrouter/free"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
FALLBACK_OPENROUTER_MODELS = (
    "openrouter/free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
)
API_KEY_ENV_NAMES = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_KEY",
    "OPENROUTER_APIKEY",
    "OPENAI_API_KEY",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch a directory for new screenshots and describe them with OpenRouter vision."
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path.cwd(),
        help="Directory to watch. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--pattern",
        default="screenshot_*.png",
        help="Glob pattern for screenshots. Defaults to screenshot_*.png.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENROUTER_VISION_MODEL", DEFAULT_OPENROUTER_MODEL),
        help=(
            "Model to use. Defaults to OPENROUTER_VISION_MODEL or "
            f"{DEFAULT_OPENROUTER_MODEL}."
        ),
    )
    parser.add_argument(
        "--detail",
        choices=("auto", "low", "high", "original"),
        default=os.getenv("OPENROUTER_VISION_DETAIL", "high"),
        help="Image detail level. Defaults to OPENROUTER_VISION_DETAIL or high.",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt used to describe each screenshot.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between directory scans. Defaults to 2.0.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=1.0,
        help="How long a file must stay unchanged before it is processed. Defaults to 1.0.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=200,
        help="Maximum output tokens for the model response. Defaults to 200.",
    )
    parser.add_argument(
        "--log-file",
        default="screenshot_descriptions.jsonl",
        help="Path to the JSONL log file. Relative paths are resolved from the watch directory.",
    )
    parser.add_argument(
        "--state-file",
        default=".screenshot_watcher_state.json",
        help="Path to the state file used to avoid duplicate processing.",
    )
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="Process matching screenshots that already exist when the script starts.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process available matches once and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect screenshots without calling the API or writing the log/state files.",
    )
    args = parser.parse_args()
    if args.once:
        args.include_existing = True
    return args


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def key_lookup_hint(base_directory: Path) -> str:
    env_names = ", ".join(API_KEY_ENV_NAMES)
    env_files = ", ".join(str(base_directory / name) for name in (".env", ".env.local"))
    return f"Checked env vars: {env_names}. Checked files: {env_files}."


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


def get_openrouter_api_key_info() -> tuple[str | None, str | None]:
    for name in API_KEY_ENV_NAMES:
        value = os.getenv(name)
        if value:
            return value, name
    return None, None


def resolve_output_path(base_directory: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return base_directory / path


def load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"processed": {}}

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not read state file {state_path}: {exc}", file=sys.stderr)
        return {"processed": {}}

    processed = data.get("processed", {})
    if not isinstance(processed, dict):
        return {"processed": {}}
    return {"processed": processed}


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def iter_matches(directory: Path, pattern: str) -> list[Path]:
    try:
        matches = [path for path in directory.glob(pattern) if path.is_file()]
    except OSError as exc:
        print(f"Warning: could not scan {directory}: {exc}", file=sys.stderr)
        return []

    def sort_key(path: Path) -> tuple[int, str]:
        try:
            stat = path.stat()
            return (stat.st_mtime_ns, path.name.lower())
        except OSError:
            return (0, path.name.lower())

    return sorted(matches, key=sort_key)


def file_signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def state_key(path: Path) -> str:
    return str(path.resolve())


def is_processed(state: dict[str, Any], path: Path, signature: dict[str, int]) -> bool:
    entry = state["processed"].get(state_key(path))
    if not isinstance(entry, dict):
        return False
    return entry.get("size") == signature["size"] and entry.get("mtime_ns") == signature["mtime_ns"]


def mark_processed(
    state: dict[str, Any],
    path: Path,
    signature: dict[str, int],
    *,
    model: str,
    description: str | None,
    skipped_existing: bool = False,
) -> None:
    entry: dict[str, Any] = {
        "size": signature["size"],
        "mtime_ns": signature["mtime_ns"],
        "model": model,
        "updated_at": iso_now(),
    }
    if description is not None:
        entry["description"] = description
    if skipped_existing:
        entry["skipped_existing"] = True
    state["processed"][state_key(path)] = entry


def seed_existing_matches(
    state: dict[str, Any],
    directory: Path,
    pattern: str,
    model: str,
) -> int:
    seeded = 0
    for path in iter_matches(directory, pattern):
        try:
            signature = file_signature(path)
        except OSError:
            continue
        if is_processed(state, path, signature):
            continue
        mark_processed(state, path, signature, model=model, description=None, skipped_existing=True)
        seeded += 1
    return seeded


def wait_for_stable_file(
    path: Path,
    settle_seconds: float,
    max_wait_seconds: float = 60.0,
) -> dict[str, int] | None:
    deadline = time.monotonic() + max_wait_seconds
    last_signature: dict[str, int] | None = None
    stable_since: float | None = None
    sleep_interval = max(0.2, min(settle_seconds / 2, 1.0))

    while time.monotonic() < deadline:
        try:
            signature = file_signature(path)
        except OSError:
            time.sleep(sleep_interval)
            continue

        if signature["size"] <= 0:
            time.sleep(sleep_interval)
            continue

        if signature == last_signature:
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= settle_seconds:
                return signature
        else:
            last_signature = signature
            stable_since = None

        time.sleep(sleep_interval)

    return None


def make_data_url(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        mime_type = "application/octet-stream"
    image_bytes = path.read_bytes()
    base64_image = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{base64_image}"


def build_model_attempts(primary_model: str) -> list[str]:
    attempts: list[str] = []
    for model_name in (primary_model, *FALLBACK_OPENROUTER_MODELS):
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


def summarize_empty_response(payload: dict[str, Any]) -> str:
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
    content_type = type(content).__name__
    return (
        f"finish_reason={finish_reason!r}, refusal={refusal!r}, "
        f"content_type={content_type}"
    )


def send_openrouter_request(
    *,
    api_key: str,
    base_url: str,
    image_path: Path,
    model: str,
    detail: str,
    prompt: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": make_data_url(image_path),
                            "detail": detail,
                        },
                    },
                ],
            }
        ],
        "max_tokens": max_output_tokens,
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
        with request.urlopen(api_request, timeout=120) as response:
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
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenRouter API returned invalid JSON.") from exc


def describe_image(
    image_path: Path,
    *,
    model: str,
    detail: str,
    prompt: str,
    max_output_tokens: int,
) -> tuple[str, str]:
    api_key = get_openrouter_api_key()
    if not api_key:
        raise RuntimeError(
            "OpenRouter key not found. Set OPENROUTER_API_KEY or put it in .env / .env.local."
        )

    base_url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL).rstrip("/")
    last_empty_summary = "no response summary available"
    last_error = "no error details available"

    for candidate_model in build_model_attempts(model):
        try:
            response_payload = send_openrouter_request(
                api_key=api_key,
                base_url=base_url,
                image_path=image_path,
                model=candidate_model,
                detail=detail,
                prompt=prompt,
                max_output_tokens=max_output_tokens,
            )
        except RuntimeError as exc:
            last_error = str(exc)
            print(
                f"[{iso_now()}] {candidate_model} failed; trying next fallback. {last_error}",
                file=sys.stderr,
            )
            continue

        description = extract_output_text(response_payload)
        if description:
            return description, candidate_model

        last_empty_summary = summarize_empty_response(response_payload)
        print(
            f"[{iso_now()}] {candidate_model} returned no text; trying next fallback.",
            file=sys.stderr,
        )

    raise RuntimeError(
        "OpenRouter did not produce a usable description after trying all configured free vision models. "
        f"Last response summary: {last_empty_summary}. Last error: {last_error}"
    )


def append_log(
    log_path: Path,
    *,
    image_path: Path,
    model: str,
    detail: str,
    signature: dict[str, int],
    description: str,
) -> None:
    record = {
        "described_at": iso_now(),
        "file": str(image_path.resolve()),
        "filename": image_path.name,
        "model": model,
        "detail": detail,
        "size": signature["size"],
        "mtime_ns": signature["mtime_ns"],
        "description": description,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def process_path(
    path: Path,
    *,
    args: argparse.Namespace,
    state: dict[str, Any],
    log_path: Path,
) -> bool:
    signature = wait_for_stable_file(path, args.settle_seconds)
    if signature is None:
        print(f"Skipped {path.name}: file never became stable.", file=sys.stderr)
        return False

    if is_processed(state, path, signature):
        return False

    print(f"[{iso_now()}] Detected {path.name}")

    if args.dry_run:
        description = "Dry run: no API call made."
        used_model = args.model
    else:
        description, used_model = describe_image(
            path,
            model=args.model,
            detail=args.detail,
            prompt=args.prompt,
            max_output_tokens=args.max_output_tokens,
        )

    if used_model != args.model:
        print(f"Used fallback model: {used_model}")

    print(description)
    print("")

    mark_processed(state, path, signature, model=used_model, description=description)

    if not args.dry_run:
        append_log(
            log_path,
            image_path=path,
            model=used_model,
            detail=args.detail,
            signature=signature,
            description=description,
        )

    return True


def run(args: argparse.Namespace) -> int:
    directory = args.directory.resolve()
    if not directory.exists() or not directory.is_dir():
        print(f"Directory not found: {directory}", file=sys.stderr)
        return 1

    bootstrap_environment(directory)
    api_key, api_key_source = get_openrouter_api_key_info()

    if api_key:
        print(
            f"OpenRouter API key detected from {api_key_source}: {mask_secret(api_key)}"
        )
    else:
        print(
            "OpenRouter API key not found. " + key_lookup_hint(directory),
            file=sys.stderr,
        )

    if not args.dry_run and not api_key:
        print(
            (
                "OpenRouter key not found. Set OPENROUTER_API_KEY or add it to .env / .env.local. "
                + key_lookup_hint(directory)
            ),
            file=sys.stderr,
        )
        return 1

    log_path = resolve_output_path(directory, args.log_file)
    state_path = resolve_output_path(directory, args.state_file)

    if not args.dry_run:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.parent.mkdir(parents=True, exist_ok=True)

    state = load_state(state_path)

    if not args.include_existing:
        seeded = seed_existing_matches(state, directory, args.pattern, args.model)
        if seeded and not args.dry_run:
            save_state(state_path, state)

    print(f"Watching {directory} for {args.pattern}")
    print(
        f"Model={args.model} detail={args.detail} poll={args.poll_interval}s settle={args.settle_seconds}s"
    )
    if args.dry_run:
        print("Dry run mode is enabled. No API calls or log/state writes will be made.")
    print("")

    try:
        while True:
            wrote_state = False

            for path in iter_matches(directory, args.pattern):
                changed = process_path(path, args=args, state=state, log_path=log_path)
                if changed and not args.dry_run:
                    save_state(state_path, state)
                    wrote_state = True

            if args.once:
                break

            if not wrote_state:
                time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
