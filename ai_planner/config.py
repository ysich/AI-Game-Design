from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit


DEFAULT_MODEL_CONFIG: Dict[str, Any] = {
    "provider": "local",
    "model": "local-rule-v1",
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "temperature": 0.4,
    "max_tokens": 4000,
    "timeout_seconds": 60,
}

PUBLIC_MODEL_FIELDS = (
    "provider",
    "model",
    "base_url",
    "temperature",
    "max_tokens",
    "timeout_seconds",
)


def _number(value: Any, field: str, minimum: float, maximum: float, integer: bool = False) -> Any:
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数字") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{field} 必须在 {minimum:g} 到 {maximum:g} 之间")
    return number


def normalize_model_config(payload: Dict[str, Any] | None, current: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Validate user settings while allowing a blank API key to keep the saved key."""
    source = dict(DEFAULT_MODEL_CONFIG)
    source.update(current or {})
    source.update(payload or {})

    provider = str(source.get("provider", "local")).strip() or "local"
    if provider not in {"local", "openai_compatible"}:
        raise ValueError("provider 只能是 local 或 openai_compatible")

    model = str(source.get("model", "")).strip()
    if not model:
        raise ValueError("模型名称不能为空")
    if len(model) > 120:
        raise ValueError("模型名称过长")

    base_url = str(source.get("base_url", DEFAULT_MODEL_CONFIG["base_url"])).strip().rstrip("/")
    if provider == "openai_compatible":
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OpenAI 兼容接口地址必须是 http 或 https URL")

    api_key = source.get("api_key", "")
    api_key = str(api_key) if api_key is not None else ""
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "temperature": _number(source.get("temperature", 0.4), "temperature", 0, 2),
        "max_tokens": _number(source.get("max_tokens", 4000), "max_tokens", 256, 32000, integer=True),
        "timeout_seconds": _number(source.get("timeout_seconds", 60), "timeout_seconds", 5, 300, integer=True),
    }


def public_model_config(config: Dict[str, Any]) -> Dict[str, Any]:
    result = {field: config.get(field, DEFAULT_MODEL_CONFIG[field]) for field in PUBLIC_MODEL_FIELDS}
    result["api_key_configured"] = bool(config.get("api_key"))
    return result


class ModelConfigStore:
    """Persist model settings separately from workflow snapshots."""

    def __init__(self, root: Path | str):
        self.path = Path(root) / ".ai-planner" / "model-config.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return dict(DEFAULT_MODEL_CONFIG)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"模型配置读取失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("模型配置必须是 JSON 对象")
        return normalize_model_config(payload)

    def save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        current = self.load()
        # An empty form field means the existing secret remains unchanged.
        if not str(payload.get("api_key", "")):
            payload = dict(payload)
            payload["api_key"] = current.get("api_key", "")
        if payload.get("clear_api_key"):
            payload = dict(payload)
            payload["api_key"] = ""
        config = normalize_model_config(payload, current=current)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
        return config
