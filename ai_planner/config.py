from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit


DEFAULT_TEXT_CONFIG: Dict[str, Any] = {
    "provider": "local",
    "model": "local-rule-v1",
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "temperature": 0.4,
    "max_tokens": 4000,
    "timeout_seconds": 60,
}

DEFAULT_IMAGE_CONFIG: Dict[str, Any] = {
    "provider": "disabled",
    "model": "gpt-image-1",
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "size": "1024x1024",
    "quality": "auto",
    "timeout_seconds": 120,
}

DEFAULT_SEARCH_CONFIG: Dict[str, Any] = {
    "provider": "bing",
    "base_url": "https://www.bing.com/search",
    "api_key": "",
    "timeout_seconds": 15,
    "max_results": 5,
    "region": "wt-wt",
    "safe_search": True,
    "auto_context": False,
}

# These are the image sizes supported by the OpenAI-compatible image contract.
# Keep the list shared by config validation and per-generation overrides.
SUPPORTED_IMAGE_SIZES = frozenset({
    "1792x768",  # 21:9
    "1536x864",  # 16:9
    "1536x1024",  # 3:2
    "1365x1024",  # 4:3
    "1024x1024",  # 1:1
    "1024x1365",  # 3:4
    "1024x1536",  # 2:3
    "864x1536",  # 9:16
    "768x1792",  # 9:21
    "auto",
})


def _number(value: Any, field: str, minimum: float, maximum: float, integer: bool = False) -> Any:
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数字") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{field} 必须在 {minimum:g} 到 {maximum:g} 之间")
    return number


def _boolean(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return bool(value) if value is not None else default


def _endpoint(value: Any, provider: str, field: str) -> str:
    url = str(value).strip().rstrip("/")
    if provider == "openai_compatible":
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{field}必须是 http 或 https URL")
    return url


def _text_config(payload: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    source = dict(DEFAULT_TEXT_CONFIG)
    source.update(current)
    source.update(payload)
    provider = str(source.get("provider", "local")).strip() or "local"
    if provider not in {"local", "openai_compatible"}:
        raise ValueError("文本模型提供商只能是 local 或 openai_compatible")
    model = str(source.get("model", "")).strip()
    if not model or len(model) > 120:
        raise ValueError("文本模型名称不能为空且不能超过 120 个字符")
    return {
        "provider": provider,
        "model": model,
        "base_url": _endpoint(source.get("base_url", DEFAULT_TEXT_CONFIG["base_url"]), provider, "文本模型接口地址"),
        "api_key": str(source.get("api_key", "") or ""),
        "temperature": _number(source.get("temperature", 0.4), "temperature", 0, 2),
        "max_tokens": _number(source.get("max_tokens", 4000), "max_tokens", 256, 32000, integer=True),
        "timeout_seconds": _number(source.get("timeout_seconds", 60), "文本模型超时", 5, 300, integer=True),
    }


def _image_config(payload: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    source = dict(DEFAULT_IMAGE_CONFIG)
    source.update(current)
    source.update(payload)
    provider = str(source.get("provider", "disabled")).strip() or "disabled"
    if provider not in {"disabled", "openai_compatible"}:
        raise ValueError("图片模型提供商只能是 disabled 或 openai_compatible")
    model = str(source.get("model", "")).strip()
    if not model or len(model) > 120:
        raise ValueError("图片模型名称不能为空且不能超过 120 个字符")
    size = str(source.get("size", "1024x1024")).strip()
    if size not in SUPPORTED_IMAGE_SIZES:
        raise ValueError("图片尺寸不受支持")
    quality = str(source.get("quality", "auto")).strip()
    if quality not in {"auto", "low", "medium", "high", "standard", "hd"}:
        raise ValueError("图片质量不受支持")
    return {
        "provider": provider,
        "model": model,
        "base_url": _endpoint(source.get("base_url", DEFAULT_IMAGE_CONFIG["base_url"]), provider, "图片模型接口地址"),
        "api_key": str(source.get("api_key", "") or ""),
        "size": size,
        "quality": quality,
        "timeout_seconds": _number(source.get("timeout_seconds", 120), "图片模型超时", 5, 600, integer=True),
    }


def _search_config(payload: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    source = dict(DEFAULT_SEARCH_CONFIG)
    source.update(current)
    source.update(payload)
    provider = str(source.get("provider", "bing")).strip() or "bing"
    if provider not in {"disabled", "bing", "duckduckgo", "tavily", "serper"}:
        raise ValueError("联网搜索提供商只能是 disabled、bing、duckduckgo、tavily 或 serper")
    base_url = _endpoint(source.get("base_url", DEFAULT_SEARCH_CONFIG["base_url"]), provider, "联网搜索接口地址")
    if provider == "tavily" and "base_url" not in payload and current.get("base_url", DEFAULT_SEARCH_CONFIG["base_url"]) == DEFAULT_SEARCH_CONFIG["base_url"]:
        base_url = "https://api.tavily.com/search"
    if provider == "serper" and "base_url" not in payload and current.get("base_url", DEFAULT_SEARCH_CONFIG["base_url"]) == DEFAULT_SEARCH_CONFIG["base_url"]:
        base_url = "https://google.serper.dev/search"
    region = str(source.get("region", "wt-wt")).strip()[:20] or "wt-wt"
    return {
        "provider": provider,
        "base_url": base_url,
        "api_key": str(source.get("api_key", "") or ""),
        "timeout_seconds": _number(source.get("timeout_seconds", 15), "联网搜索超时", 3, 120, integer=True),
        "max_results": _number(source.get("max_results", 5), "联网搜索结果数", 1, 10, integer=True),
        "region": region,
        "safe_search": _boolean(source.get("safe_search", True), True),
        "auto_context": _boolean(source.get("auto_context", False)),
    }


def _as_nested(config: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    value = config or {}
    if "text" in value or "image" in value or "search" in value:
        return {
            "text": dict(value.get("text") or {}),
            "image": dict(value.get("image") or {}),
            "search": dict(value.get("search") or {}),
        }
    # Migrate the first version, where text settings lived at the top level.
    return {"text": dict(value), "image": {}, "search": {}}


def normalize_model_config(payload: Dict[str, Any] | None, current: Dict[str, Any] | None = None) -> Dict[str, Dict[str, Any]]:
    incoming = _as_nested(payload)
    saved = _as_nested(current)
    return {
        "text": _text_config(incoming["text"], saved["text"]),
        "image": _image_config(incoming["image"], saved["image"]),
        "search": _search_config(incoming["search"], saved["search"]),
    }


def public_model_config(config: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for kind in ("text", "image", "search"):
        section = config[kind]
        result[kind] = {key: value for key, value in section.items() if key != "api_key"}
        result[kind]["api_key_configured"] = bool(section.get("api_key"))
    return result


class ModelConfigStore:
    """Persist text and image model settings separately from workflow snapshots."""

    def __init__(self, root: Path | str):
        self.path = Path(root) / ".ai-planner" / "model-config.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return normalize_model_config({})
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"模型配置读取失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("模型配置必须是 JSON 对象")
        return normalize_model_config(payload)

    def save(self, payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        current = self.load()
        incoming = _as_nested(payload)
        for kind in ("text", "image", "search"):
            section = incoming[kind]
            if not str(section.get("api_key", "")):
                section["api_key"] = current[kind].get("api_key", "")
            if section.pop("clear_api_key", False):
                section["api_key"] = ""
        config = normalize_model_config(incoming, current=current)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
        return config
