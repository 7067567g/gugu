"""gugu-design Agent 面 —— design_generate 工具 + 引导式意图硬校验。

意图协议（移植 dsh-design-mode `design_generate_image` 的门禁语义，MIT）：
- 生成前 agent 必须先通过 `clarify` 完成 **≥3 轮**需求澄清（每轮 1 问 + 3 选项：
  主体/风格/构图这类生产级决策）；
- 计数由 `post_tool_call` hook 观察 **clarify 工具的真实成功调用**——服务端事实，
  模型无法靠自述绕过（dsh 把"提示词自律"变成"协议强制"的核心思路）；
- `design_generate` 调用时校验轮次：不足返回引导性错误（拒绝生成并告知还差几轮），
  达标则执行生成并清空计数（下一次生成重新走澄清）；
- dsh 完整形态还校验"每问恰 3 选项 + round 连续"；Hermes 的 clarify 本身已结构化
  （≤4 choices、桌面卡片渲染），v1 简化为成功轮次计数，形状约束写进工具 description。

生成执行与桌面 Design 画布同源：聚合网关（hermes_cli.gugu_service 的登录配置），
浏览器/工具都不直连 provider。产物落 HERMES_HOME/designs/generated/。
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TOOLSET = "gugu-design"

# clarify 成功轮次计数（进程内，按 session_id 分键；dsh intentProgress 的轻量等价物，
# 先例：tools/clarify_gateway.py 的模块级 session dict）。重启清零可接受：
# 澄清是回合内工作流，不跨重启续算。
_intent_rounds: Dict[str, int] = {}
_rounds_lock = threading.Lock()

REQUIRED_ROUNDS = 3
_VIDEO_POLL_S = 10.0
_VIDEO_TIMEOUT_S = 600.0


def _bump_round(session_id: str) -> None:
    with _rounds_lock:
        _intent_rounds[session_id] = _intent_rounds.get(session_id, 0) + 1


def _rounds(session_id: str) -> int:
    with _rounds_lock:
        return _intent_rounds.get(session_id, 0)


def _clear_rounds(session_id: str) -> None:
    with _rounds_lock:
        _intent_rounds.pop(session_id, None)


# ---------------------------------------------------------------------------
# Hook：观察 clarify 成功调用 → 意图轮次 +1
# ---------------------------------------------------------------------------

def _on_post_tool_call(tool_name: str = "", session_id: str = "", status: str = "", **_kw) -> None:
    if tool_name == "clarify" and status in ("ok", "success", ""):
        key = session_id or "__anon__"
        _bump_round(key)
        logger.debug("gugu-design: clarify round counted for session %s", key[:12])


# ---------------------------------------------------------------------------
# 工具：design_generate
# ---------------------------------------------------------------------------

def _gateway() -> tuple[str, str]:
    from hermes_cli.gugu_service import gateway_credentials

    return gateway_credentials()


def _generated_dir() -> Path:
    from hermes_constants import get_default_hermes_root

    d = Path(get_default_hermes_root()) / "designs" / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


SCHEMA = {
    "name": "design_generate",
    "description": (
        "咕咕设计模式生成工具（图像/视频），经聚合网关执行。"
        "调用前必须先完成至少 3 轮设计澄清：每轮调用一次 clarify 工具，"
        "只问 1 个问题并给恰好 3 个互斥选项（如主体、风格/材质、构图/画幅、时长节奏），"
        "把用户选择累积成生产级 prompt。澄清不足 3 轮时本工具会拒绝并返回当前进度。"
        "生成成功后澄清计数清零（下一次生成需重新澄清）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["image", "video"], "description": "生成类型"},
            "prompt": {"type": "string", "description": "累积澄清后的完整生产级提示词"},
            "model": {"type": "string", "description": "网关模型名（省略则用默认：图像 qwen-image-2.0 / 视频 wan3.0-video-prime）"},
            "size": {"type": "string", "description": "图像尺寸，如 1024x1024 / 1536x1024"},
            "duration": {"type": "integer", "description": "视频时长（秒，4-15）"},
        },
        "required": ["kind", "prompt"],
    },
}


def _handle_design_generate(args: Dict[str, Any], session_id: Optional[str] = None) -> str:
    import httpx

    key = session_id or "__anon__"
    rounds = _rounds(key)
    if rounds < REQUIRED_ROUNDS:
        return json.dumps({
            "error": (
                f"引导式意图澄清未完成：已 {rounds} 轮，需至少 {REQUIRED_ROUNDS} 轮。"
                "请先用 clarify 工具逐轮澄清（每轮 1 问 + 恰好 3 个互斥选项，"
                "覆盖主体/风格/构图/画幅等决策），把用户选择累积进 prompt 后再生成。"
            ),
            "intent_rounds": rounds,
            "required_rounds": REQUIRED_ROUNDS,
        })

    kind = str(args.get("kind") or "image")
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return json.dumps({"error": "prompt 不能为空"})

    base_url, api_key = _gateway()
    if not base_url or not api_key:
        return json.dumps({"error": "尚未登录咕咕聚合网关（设置 → 咕咕聚合网关 → 网页授权）"})

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    out_dir = _generated_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")

    try:
        with httpx.Client(timeout=180.0) as client:
            if kind == "image":
                body: Dict[str, Any] = {"model": str(args.get("model") or "qwen-image-2.0"), "prompt": prompt, "n": 1}
                if args.get("size"):
                    body["size"] = args["size"]
                resp = client.post(f"{base_url}/images/generations", headers=headers, json=body)
                if resp.status_code != 200:
                    return json.dumps({"error": f"网关 HTTP {resp.status_code}: {resp.text[:200]}"})
                item = (resp.json().get("data") or [{}])[0]
                data = base64.b64decode(item["b64_json"]) if item.get("b64_json") else None
                if data is None and item.get("url"):
                    got = client.get(item["url"], timeout=120.0)
                    got.raise_for_status()
                    data = got.content
                if not data:
                    return json.dumps({"error": "网关未返回图像内容"})
                path = out_dir / f"{stamp}-{uuid.uuid4().hex[:8]}.png"
                path.write_bytes(data)
                _clear_rounds(key)
                return json.dumps({"success": True, "kind": "image", "path": str(path), "model": body["model"], "prompt": prompt})

            if kind == "video":
                vbody: Dict[str, Any] = {"model": str(args.get("model") or "wan3.0-video-prime"), "prompt": prompt,
                                         "duration": str(int(args.get("duration") or 5))}
                resp = client.post(f"{base_url}/videos", headers=headers, json=vbody)
                if resp.status_code not in (200, 201, 202):
                    return json.dumps({"error": f"网关 HTTP {resp.status_code}: {resp.text[:200]}"})
                task_id = resp.json().get("id", "")
                deadline = time.time() + _VIDEO_TIMEOUT_S
                while time.time() < deadline:
                    time.sleep(_VIDEO_POLL_S)
                    st = client.get(f"{base_url}/videos/{task_id}", headers={"Authorization": f"Bearer {api_key}"})
                    if st.status_code != 200:
                        continue
                    status = str(st.json().get("status") or "")
                    if status in ("completed", "succeeded", "finished"):
                        break
                    if status in ("failed", "cancelled", "expired"):
                        return json.dumps({"error": f"上游视频任务 {status}"})
                else:
                    return json.dumps({"error": "视频任务轮询超时", "task_id": task_id})
                content = client.get(f"{base_url}/videos/{task_id}/content", timeout=300.0)
                content.raise_for_status()
                path = out_dir / f"{stamp}-{uuid.uuid4().hex[:8]}.mp4"
                path.write_bytes(content.content)
                _clear_rounds(key)
                return json.dumps({"success": True, "kind": "video", "path": str(path), "model": vbody["model"], "prompt": prompt})

            return json.dumps({"error": f"kind 必须是 image 或 video，收到 {kind!r}"})
    except httpx.HTTPError as exc:
        return json.dumps({"error": f"网关请求失败: {exc}"})
    except Exception as exc:  # noqa: BLE001 — 工具面兜底，错误回给模型
        logger.exception("design_generate failed")
        return json.dumps({"error": str(exc)[:300]})


def _available() -> bool:
    base, key = _gateway()
    return bool(base and key)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Plugin entry point：注册 design_generate 工具与 clarify 轮次观察 hook。"""
    ctx.register_tool(
        name="design_generate", toolset=TOOLSET, schema=SCHEMA,
        handler=_handle_design_generate, check_fn=_available,
        description="咕咕设计生成（需先完成 3 轮 clarify 意图澄清）", emoji="🎨",
    )
    ctx.register_hook("post_tool_call", _on_post_tool_call)
