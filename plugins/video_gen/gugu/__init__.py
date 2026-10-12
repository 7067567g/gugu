"""咕咕聚合网关视频生成后端。

网关把多家厂商的视频模型统一成 OpenAI ``/v1/videos`` 异步任务形状（create →
retrieve → download_content），所以全部 SDK 管线复用
:class:`agent.video_gen_provider.OpenAICompatibleVideoGenProvider`。
与 deepinfra/openrouter 的区别：凭据与 base_url 不来自独立 env key，而是复用
桌面「咕咕网页授权」登录写入的网关配置（``hermes_cli.gugu_service``）；模型目录
live 拉 ``{base}/models?modality=video``，厂商入驻即自动出现、下线即消失。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.video_gen_provider import OpenAICompatibleVideoGenProvider

logger = logging.getLogger(__name__)


def _gateway() -> tuple[str, str]:
    from hermes_cli.gugu_service import gateway_credentials

    return gateway_credentials()


class GuguVideoGenProvider(OpenAICompatibleVideoGenProvider):
    name = "gugu"
    display_name = "咕咕聚合网关"
    # 基类用 _env_key/_default_base_url 解析凭据；这里覆写两个方法直读网关配置。
    _env_key = "GUGU_GATEWAY_API_KEY"
    _default_base_url = ""

    def _api_key(self) -> str:
        return _gateway()[1]

    def _base_url(self) -> str:
        return _gateway()[0] or self._default_base_url

    def is_available(self) -> bool:
        base, key = _gateway()
        return bool(base and key)

    def list_models(self) -> List[Dict[str, Any]]:
        """视频模型目录：网关 /v1/models?modality=video（无缓存，网关 5s 热更新）。"""
        base, key = _gateway()
        if not base or not key:
            return []
        try:
            import httpx

            resp = httpx.get(
                f"{base}/models", params={"modality": "video"},
                headers={"Authorization": f"Bearer {key}"}, timeout=10.0,
            )
            if resp.status_code != 200:
                return []
            return [{"id": m["id"], "display": m["id"]} for m in resp.json().get("data", []) if m.get("id")]
        except Exception as exc:  # noqa: BLE001 — 目录失败不崩 picker
            logger.debug("gugu video catalog fetch failed: %s", exc)
            return []

    def capabilities(self) -> Dict[str, Any]:
        return {"modalities": ["text", "image"], "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "resolutions": ["720p", "1080p"], "max_duration": 15, "min_duration": 4,
                "supports_audio": True, "supports_negative_prompt": True, "supports_seed": False,
                "supports_upscale": False, "max_reference_images": 2}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {"name": "咕咕聚合网关", "badge": "byo-gateway",
                "tag": "百炼 wan3.0/happyhorse 等 —— 随桌面咕咕登录自动可用，无需单独 key",
                "env_vars": []}


def register(ctx) -> None:
    """Plugin entry point — wire ``GuguVideoGenProvider`` into the registry."""
    ctx.register_video_gen_provider(GuguVideoGenProvider())
