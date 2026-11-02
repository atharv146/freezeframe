"""Optional: plain-English descriptions of unfamiliar apps, written by Claude.

Off unless you add your own Anthropic API key (freezeframe ai --key, or the
dashboard). Only app names, bundle identifiers, developers and categories are
sent, never file paths, window titles or anything you've typed. Each app is
described once and the answer is kept in the recording.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from .paths import data_dir

log = logging.getLogger("freezeframe.ai")

MODEL = "claude-opus-5-5"
MAX_APPS = 12

SYSTEM = """You explain the apps and background processes running on someone's computer, for people who aren't technical.
For each item, say what it is in one short sentence a teenager or a grandparent would follow, using the details given
(name, bundle identifier, developer, category). Then say what normal resource use looks like and whether it's fine to
leave running. If you don't recognise it and the details don't make it clear, set known to false and describe only what
the details show (for example "An app made by X"), never a guess presented as fact. No marketing language."""

SCHEMA = {
    "type": "object",
    "properties": {
        "apps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "known": {"type": "boolean", "description": "true if you recognise this specific app or process"},
                    "what": {"type": "string", "description": "one sentence: what it is, starting lowercase, e.g. 'a notes app that records your meetings.'"},
                    "kind": {"type": "string", "enum": ["app", "background", "system"],
                             "description": "app: something you open and use; background: a helper or menu-bar tool; system: part of the operating system"},
                    "normal": {"type": "string", "description": "one sentence on typical memory and CPU use"},
                    "stay_open": {"type": "string", "description": "one sentence: is it fine to leave running, and when to quit it"},
                },
                "required": ["name", "known", "what", "kind", "normal", "stay_open"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["apps"],
    "additionalProperties": False,
}


def _key_file():
    return data_dir() / "anthropic-key"


def api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        return _key_file().read_text().strip() or None
    except OSError:
        return None


def save_key(key: str) -> None:
    path = _key_file()
    path.write_text(key.strip())
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def forget_key() -> None:
    _key_file().unlink(missing_ok=True)


def installed() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def status() -> dict[str, Any]:
    return {"installed": installed(), "enabled": api_key() is not None}


class AIError(Exception):
    """A failure worth showing to the person, in plain words."""


def describe(apps: list[dict[str, Any]], platform: str = sys.platform) -> dict[str, dict[str, Any]]:
    """{app name: {known, what, kind, normal, stay_open}} for up to MAX_APPS apps."""
    import anthropic

    key = api_key()
    if not key:
        raise AIError("No Anthropic API key is set.")
    os_name = {"darwin": "macOS", "win32": "Windows"}.get(platform, "Linux")
    items = [{k: v for k, v in a.items() if v} for a in apps[:MAX_APPS]]
    client = anthropic.Anthropic(api_key=key, timeout=60.0, max_retries=2)
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            # If the request is declined, Anthropic retries it on its recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": f"Running on {os_name}:\n{json.dumps(items, indent=1)}"}],
        )
    except anthropic.AuthenticationError as e:
        raise AIError("Anthropic didn't accept the API key. Check it at console.anthropic.com.") from e
    except anthropic.PermissionDeniedError as e:
        raise AIError("This API key isn't allowed to use Claude. Check your Anthropic account.") from e
    except anthropic.RateLimitError as e:
        raise AIError("Anthropic is rate-limiting this key right now. Freezeframe will try again later.") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"Anthropic returned an error ({e.status_code}). Freezeframe will try again later.") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Couldn't reach Anthropic. Check the internet connection.") from e
    if response.stop_reason == "refusal":
        return {}
    text = next((b.text for b in reversed(response.content) if b.type == "text"), "")
    try:
        rows = json.loads(text)["apps"]
    except (ValueError, KeyError) as e:
        log.warning("unexpected AI response: %s", text[:200])
        raise AIError("Claude's answer couldn't be read. Freezeframe will try again later.") from e
    wanted = {a["name"] for a in items}
    return {r["name"]: {k: v for k, v in r.items() if k != "name"} for r in rows if r.get("name") in wanted}
