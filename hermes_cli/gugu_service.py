"""gugu 聚合网关登录服务（深 fork 定制，非上游 Hermes 功能）。

上游的 Nous Portal OAuth 在 gugu 不适用：登录改为「打开网页授权页 → 下发聚合
网关 key → 校验 → 保存」。协议设计上复用 dashboard 的 device_code 会话机制——
会话注册在 ``web_routers.oauth._oauth_sessions``，桌面 onboarding 的既有状态机
（start → 浏览器打开 verification_url → pollOAuthSession → approved →
confirming_model）原样可用，渲染层零改动。

- 授权页由 web_server 自托管在 ``/gugu/auth/page``（非 /api 路径，不受 dashboard
  auth 网关约束，与 SPA 同形；桌面服务只绑 127.0.0.1 回环）。
- key 校验通过后的持久化路径与 custom 端点向导 ``_model_flow_custom`` 完全一致：
  密钥写进 ``~/.hermes/.env``（按 host:port 命名），model 段指向网关，并同步到
  ``custom_providers`` 菜单。
- 后续集成 api-net：把 ``GUGU_AUTH_URL`` 指向真实登录页（该页按本协议回投
  /api/gugu/auth/complete），本模块流程不变；网关地址用
  ``GUGU_GATEWAY_BASE_URL`` 覆盖。
"""

from __future__ import annotations

import os
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_GATEWAY_BASE_URL = "https://ai.joydove.cn/v1"
GUGU_PROVIDER_NAME = "咕咕聚合网关"
# 登录成功后的默认模型：在网关 /models 列表里按优先级挑选（通用长上下文对话优先）。
_MODEL_PREFERENCE = (
    "qwen3.7-max",
    "qwen3.8-max",
    "glm-5.3",
    "kimi-k2.6",
    "deepseek-v4-pro",
)
SESSION_TTL_SECONDS = 900


def gateway_default_base_url() -> str:
    return (os.getenv("GUGU_GATEWAY_BASE_URL") or DEFAULT_GATEWAY_BASE_URL).rstrip("/")


def auth_page_override_url() -> str:
    """集成 api-net 后把真实登录页指向这里（可选扩展点；为空时用自托管授权页）。"""
    return os.getenv("GUGU_AUTH_URL", "").strip()


def page_verification_url(dashboard_base: str, session_id: str) -> str:
    """onboarding「在浏览器中打开」的授权地址。

    ``dashboard_base`` 传 request.base_url（尾斜杠可有可无，内部归一化）。
    GUGU_AUTH_URL 配置后改指外部登录页（api-net console 集成形态），否则用本机自托管页。
    """
    override = auth_page_override_url()
    if override:
        joiner = "&" if "?" in override else "?"
        return f"{override}{joiner}gugu_sid={session_id}"
    return f"{dashboard_base.rstrip('/')}/gugu/auth/page?sid={session_id}"


def _env_name_for(base_url: str) -> str:
    from hermes_cli.config import custom_endpoint_key_env

    parsed = urllib.parse.urlparse(base_url)
    identity = parsed.hostname or ""
    if parsed.port:
        identity = f"{identity}_{parsed.port}"
    return custom_endpoint_key_env(identity)


def _model_endpoint_state() -> Tuple[bool, str, Optional[str]]:
    """返回 (configured, base_url, token_preview)：当前持久化的 custom 端点状态。"""
    from hermes_cli.config import get_env_value
    from hermes_cli.model_setup_flows_common import _load_config_model_section

    _cfg, model = _load_config_model_section()
    base_url = str(model.get("base_url") or "").rstrip("/")
    provider = str(model.get("provider") or "")
    # 裸 "custom" 与 custom:<名称> slug（persist 写 slug）都算咕咕端点。
    if not (provider == "custom" or provider.startswith("custom:")) or not base_url:
        return False, "", None
    key_ref = str(model.get("api_key") or "")
    if key_ref.startswith("${") and key_ref.endswith("}"):
        key = get_env_value(key_ref[2:-1])
    else:
        key = get_env_value(_env_name_for(base_url)) or get_env_value("OPENAI_API_KEY")
    return True, base_url, (key[-6:] if key else None)


def gateway_credentials() -> tuple:
    """(base_url, api_key) of the logged-in gugu gateway; ("", "") when not configured.
    Shared by the gugu video_gen plugin and the gugu-design backend plugin."""
    from hermes_cli.config import get_env_value
    from hermes_cli.model_setup_flows_common import _load_config_model_section

    _cfg, model = _load_config_model_section()
    base_url = str(model.get("base_url") or "").rstrip("/")
    key_ref = str(model.get("api_key") or "")
    if key_ref.startswith("${") and key_ref.endswith("}"):
        key = get_env_value(key_ref[2:-1]) or ""
    else:
        key = key_ref or get_env_value(_env_name_for(base_url)) or get_env_value("OPENAI_API_KEY") or ""
    return base_url, key


def status() -> Dict[str, Any]:
    """OAuth 目录卡片的状态（与 _copilot_acp_status 同形，被 _resolve_provider_status 直用）。"""
    configured, base_url, preview = _model_endpoint_state()
    return {
        "logged_in": bool(configured and preview),
        "source": "gugu_gateway",
        "source_label": base_url or gateway_default_base_url(),
        "token_preview": preview,
        "expires_at": None,
        "has_refresh_token": False,
        "configured": configured,
    }


def _brief(text: str) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:160]


def validate_key(api_key: str, base_url: str) -> Dict[str, Any]:
    """向网关验证 key：拉 /models（必须），顺带 /balance（失败不阻断）。

    失败抛 ValueError（文案直接给到授权页展示）。
    """
    import httpx

    api_key = (api_key or "").strip()
    base_url = (base_url or "").strip().rstrip("/") or gateway_default_base_url()
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("网关地址必须以 http:// 或 https:// 开头")
    if not api_key:
        raise ValueError("请输入聚合网关 API key")

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{base_url}/models", headers=headers)
            if resp.status_code != 200:
                raise ValueError(f"网关拒绝该 key（HTTP {resp.status_code}）：{_brief(resp.text)}")
            data = resp.json().get("data") or []
            model_ids = [str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")]
            balance: Optional[Dict[str, Any]] = None
            try:
                bal = client.get(f"{base_url}/balance", headers=headers)
                if bal.status_code == 200:
                    balance = bal.json()
            except httpx.HTTPError:
                pass
    except httpx.HTTPError as exc:
        raise ValueError(f"无法连接聚合网关：{exc}") from exc

    if not model_ids:
        raise ValueError("网关未返回任何可用模型，请确认该 key 已获授权（模型白名单）")
    return {"base_url": base_url, "model_ids": model_ids, "balance": balance}


def pick_default_model(model_ids: List[str]) -> str:
    for pref in _MODEL_PREFERENCE:
        if pref in model_ids:
            return pref
    return model_ids[0]


def persist(api_key: str, base_url: str, model_name: str) -> Dict[str, Any]:
    """以 _model_flow_custom 同款路径持久化：.env 密钥 → model 段 → custom_providers 菜单。"""
    from hermes_cli.auth import _save_model_choice
    from hermes_cli.config import save_env_value
    from hermes_cli.main_provider_setup import _save_custom_provider
    from hermes_cli.model_setup_flows_common import _commit_model_config, _load_config_model_section

    base_url = base_url.rstrip("/")
    key_env = _env_name_for(base_url)
    save_env_value(key_env, api_key)

    _save_model_choice(model_name)
    cfg, model = _load_config_model_section()
    # 用 custom_providers 条目的 slug（custom:咕咕聚合网关）而非裸 "custom"：
    # 与 picker 行 slug 一致，避免设置页合成 "custom" 幽灵回退行。
    from hermes_cli.providers import custom_provider_slug

    model["provider"] = custom_provider_slug(GUGU_PROVIDER_NAME)
    model["base_url"] = base_url
    # 旧端点的 key_env 指针会盖过下面写入的凭据（与 custom 向导同样的清理）。
    model.pop("key_env", None)
    model.pop("api_key_env", None)
    model.pop("api_mode", None)
    model["api_key"] = f"${{{key_env}}}"
    _commit_model_config(cfg)

    _save_custom_provider(base_url, api_key="", model=model_name, name=GUGU_PROVIDER_NAME, key_env=key_env)
    return {"model": model_name, "base_url": base_url}


def session_expires_at() -> float:
    return time.time() + SESSION_TTL_SECONDS
