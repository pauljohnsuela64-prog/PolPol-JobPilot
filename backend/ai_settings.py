"""Local-only Ollama configuration and JSON inference."""
import ipaddress
import json
import re
import time
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from local_access import private_request
from database import database

router = APIRouter(prefix="/ai", dependencies=[Depends(private_request)])
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
FAILURE = "Local AI is unavailable. Check the endpoint and installed model."
MODEL_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,159}")


def loopback_endpoint(value):
    parts = urlsplit(value)
    host = parts.hostname or ""
    if (parts.scheme != "http" or parts.username is not None or parts.password is not None
            or parts.path not in ("", "/") or parts.query or parts.fragment or "%" in host
            or not ipaddress.ip_address(host).is_loopback):
        raise ValueError("Use an HTTP endpoint on a numeric loopback address.")
    address = f"[{host}]" if ":" in host else host
    port = parts.port
    return f"http://{address}" + (f":{port}" if port is not None else "")


def local_name(name):
    return isinstance(name, str) and MODEL_NAME.fullmatch(name) and "cloud" not in name.lower()


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    endpoint: str = Field(default=DEFAULT_ENDPOINT, max_length=256)
    model: str = Field(default="", max_length=160)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value):
        return loopback_endpoint(value)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value):
        if value and not local_name(value):
            raise ValueError("Choose a local model.")
        return value


def load_settings():
    with database() as conn:
        row = conn.execute("SELECT enabled, endpoint, model FROM ai_settings WHERE id = 1").fetchone()
    return Settings(**row) if row else None


def request_json(endpoint, path, payload=None, *, seconds=4, limit=1024 * 1024):
    url = loopback_endpoint(endpoint) + path
    deadline = time.monotonic() + seconds
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(seconds, connect=2)) as client:
        with client.stream("GET" if payload is None else "POST", url, json=payload,
                           headers={"Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise ValueError(FAILURE)
            body = bytearray()
            for chunk in response.iter_raw():
                if time.monotonic() > deadline:
                    raise httpx.ReadTimeout("Ollama timed out")
                if len(body) + len(chunk) > limit:
                    raise ValueError(FAILURE)
                body.extend(chunk)
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError(FAILURE)
    return value


class LocalAIError(ValueError):
    """A fixed, user-facing message safe to expose to the UI."""


def safe_message(exc):
    if isinstance(exc, LocalAIError):
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return "Ollama timed out. Try again or choose a smaller local model."
    if isinstance(exc, httpx.ConnectError):
        return "Ollama is unreachable. Start Ollama and check the saved endpoint."
    return FAILURE


def remote_metadata(info):
    return any(info.get(key) for key in ("remote_model", "remote_host", "remote_url"))


def installed(endpoint):
    models = request_json(endpoint, "/api/tags")["models"]
    if not isinstance(models, list):
        raise ValueError(FAILURE)
    return sorted({item["name"] for item in models if isinstance(item, dict)
                   and local_name(item.get("name")) and not remote_metadata(item)})


def verify_model(settings, models):
    if settings.model not in models:
        raise LocalAIError("The selected model is missing. Choose an installed local model.")
    info = request_json(settings.endpoint, "/api/show", {"model": settings.model, "verbose": False},
                        limit=4 * 1024 * 1024)
    if remote_metadata(info) or info.get("details", {}).get("format") != "gguf":
        raise LocalAIError("Choose a local GGUF model. The selected model could not be verified as local.")


def describe(settings):
    if settings is None:
        return {**Settings().model_dump(), "models": [], "status": "disabled", "error": None}
    models = []
    error = None
    status = "disabled"
    try:
        models = installed(settings.endpoint)
        if settings.enabled:
            verify_model(settings, models)
            status = "ready"
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        status = "unavailable" if settings.enabled else "disabled"
        error = safe_message(exc)
    return {**settings.model_dump(), "models": models, "status": status, "error": error}


@router.get("/settings")
def get_settings():
    return describe(load_settings())


@router.put("/settings")
def put_settings(settings: Settings):
    if "endpoint" not in settings.model_fields_set:
        raise HTTPException(422, "Enter a local endpoint.")
    if settings.enabled and not settings.model:
        raise HTTPException(422, "Choose a local model to enable AI.")
    with database() as conn:
        conn.execute("""INSERT INTO ai_settings (id, enabled, endpoint, model) VALUES (1, %s, %s, %s)
                     ON CONFLICT (id) DO UPDATE SET enabled=excluded.enabled, endpoint=excluded.endpoint,
                     model=excluded.model""", (settings.enabled, settings.endpoint, settings.model))
    return describe(settings)


def infer(data: dict) -> dict:
    settings = load_settings()
    try:
        if settings is None or not settings.enabled:
            raise LocalAIError("Local AI is disabled.")
        prompt = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(prompt.encode()) > 65536:
            raise ValueError(FAILURE)
        verify_model(settings, installed(settings.endpoint))
        fields = ["fact_ids"]
        if "job_clauses" in data:
            fields.extend(["requirement_ids", "gap_ids"])
        schema = {"type": "object", "required": fields, "additionalProperties": False,
                  "properties": {key: {"type": "array", "items": {"type": "string"}} for key in fields}}
        result = request_json(settings.endpoint, "/api/generate", {
            "model": settings.model, "prompt": prompt, "format": schema, "stream": False, "think": False,
            "options": {"temperature": 0, "num_predict": 2048, "num_ctx": 8192},
        }, seconds=60, limit=128 * 1024)
        if result.get("done") is not True:
            raise ValueError(FAILURE)
        output = json.loads(result["response"])
        if not isinstance(output, dict):
            raise ValueError(FAILURE)
        return output
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise LocalAIError(safe_message(exc)) from None
