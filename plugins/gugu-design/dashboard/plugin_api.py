"""gugu-design 后端 —— 无限画布创作工作台 API，挂载于 /api/plugins/gugu-design/。

主机纪律（移植自 dsh-design-mode `packages/design/design-mode/src/index.ts` +
`storage.ts`，见 docs/02-三模式架构.md §5.2）：
- **Host 单一权威**：浏览器不存文档、不直连 provider；所有变更经本服务串行化。
- **每工程串行**：per-project 锁把「读-改-写」串成原子操作（dsh mutationTails 语义）；
  job 执行在 daemon 线程，状态迁移同样经锁，同工程多 job 可并发但文档写不交叉。
- **原子持久化**：document.json temp+rename 原子写 + journal.ndjson 审计（gugu_storage）。
- **乐观并发 + 幂等**：save 带 expected_revision；operation_id 幂等窗口（重试/重放同结果）。
- **崩溃恢复**：进程重启后首次触碰工程时，running→queued 重跑（付费调用绝不自动重试，
  但网关任务可安全重提交；running 卡死意味着进程死过，重跑是正确语义）。
- **事件推送**：/events WebSocket（dashboard canonical WS auth，token 走 query），
  job 状态与文档修订变更实时广播；前端保留轮询兜底。

生成链路：图像 ``POST {base}/images/generations``（同步）；视频 ``POST {base}/videos``
异步任务 + 轮询 + ``/content`` 下载。凭据与桌面登录同源（hermes_cli.gugu_service）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import importlib.util as _ilu

import httpx
from fastapi import APIRouter, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# 插件按 spec_from_file_location 加载（无包上下文），同目录模块用 file loader 引入。
_spec = _ilu.spec_from_file_location("gugu_design_storage", Path(__file__).with_name("gugu_storage.py"))
gs = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(gs)  # type: ignore[union-attr]

log = logging.getLogger(__name__)

router = APIRouter()

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ASSET_ID_RE = re.compile(r"^[a-f0-9]{16}$")
_VIDEO_POLL_S = 10.0
_VIDEO_TIMEOUT_S = 25 * 60.0

# per-project 文档写锁（dsh mutationTails 的同步等价物）
_project_locks: Dict[str, threading.Lock] = {}
_project_locks_guard = threading.Lock()

# WS 订阅者：pid -> set(websocket)；发送经主事件循环调度（job 线程 publish）
_subscribers: Dict[str, set] = {}
_subscribers_guard = threading.Lock()
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def _project_lock(pid: str) -> threading.Lock:
    with _project_locks_guard:
        return _project_locks.setdefault(pid, threading.Lock())


# ---------------------------------------------------------------------------
# 事件广播（/events WS）
# ---------------------------------------------------------------------------

def _publish(pid: str, payload: Dict[str, Any]) -> None:
    """线程安全广播：job 线程/请求线程都可调用。无订阅者时零成本。"""
    loop = _main_loop
    if loop is None or loop.is_closed():
        return
    with _subscribers_guard:
        socks = list(_subscribers.get(pid, ()))
    if not socks:
        return
    message = json.dumps({"project": pid, **payload}, ensure_ascii=False)

    async def _send() -> None:
        for ws in socks:
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001 — 断线由 accept 循环清理
                pass

    try:
        asyncio.run_coroutine_threadsafe(_send(), loop)
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# 存储层（原子写 + journal + 幂等）
# ---------------------------------------------------------------------------

def _designs_root() -> Path:
    from hermes_constants import get_default_hermes_root

    root = Path(get_default_hermes_root()) / "designs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _project_dir(pid: str) -> Path:
    if not _PROJECT_ID_RE.match(pid):
        raise HTTPException(status_code=400, detail=f"非法工程 id: {pid!r}")
    d = _designs_root() / pid
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="工程不存在")
    return d


def _load_doc(d: Path) -> Dict[str, Any]:
    f = d / "project.json"
    if not f.exists():
        raise HTTPException(status_code=404, detail="工程文档缺失")
    return json.loads(f.read_text(encoding="utf-8"))


def _save_doc(d: Path, doc: Dict[str, Any], kind: str = "save", operation_id: str = "") -> None:
    """原子写 + 审计。必须在持有该工程 _project_lock 时调用。"""
    gs.atomic_write_json(d / "project.json", doc)
    gs.journal_append(d, kind, int(doc.get("revision", 0)), operation_id)
    _publish(d.name, {"type": "document", "revision": doc.get("revision")})


def _new_document(name: str) -> Dict[str, Any]:
    now = time.time()
    return {
        "schemaVersion": 1,
        "revision": 1,
        "id": "",
        "name": name,
        "createdAt": now,
        "updatedAt": now,
        "viewport": {"x": 0, "y": 0, "scale": 1},
        "assets": {},
        "comments": {},
        "elements": {},
        "versions": {},
        "jobs": {},
        "recent_operation_ids": [],
        "operation_results": {},
    }


# ---------------------------------------------------------------------------
# 崩溃恢复：running → queued 重跑（dsh recover() 语义）
# ---------------------------------------------------------------------------

_recovery_done: set = set()


def _recover_project_jobs(pid: str, d: Path) -> None:
    with _project_lock(pid):
        if pid in _recovery_done:
            return
        _recovery_done.add(pid)
        try:
            doc = _load_doc(d)
        except HTTPException:
            return
        stale = [j for j in doc.get("jobs", {}).values() if j.get("status") in ("running", "queued")]
        for job in stale:
            # 重启意味着执行线程已死：重提交（网关任务幂等由新 job id 保证，旧任务产物丢弃）
            job["status"] = "queued"
            job["updatedAt"] = time.time()
        if stale:
            doc["revision"] += 1
            _save_doc(d, doc, kind="recover")
        for job in stale:
            _start_job(pid, job)


def _start_job(pid: str, job: Dict[str, Any]) -> None:
    req = job.get("request") or {}
    kind = job.get("kind")
    runner = _run_image_job if kind == "image" else _run_video_job
    threading.Thread(
        target=runner, args=(pid, job["id"], req), daemon=True,
        name=f"gugu-design-{job['id'][:6]}",
    ).start()


# ---------------------------------------------------------------------------
# 网关凭据（与桌面登录同源：model.base_url + api_key/${ENV}）
# ---------------------------------------------------------------------------

def _gateway() -> tuple[str, str]:
    from hermes_cli.gugu_service import gateway_credentials

    base_url, api_key = gateway_credentials()
    if not base_url or not api_key:
        raise HTTPException(status_code=400, detail="尚未登录咕咕聚合网关（请先在设置中完成网页授权）")
    return base_url.rstrip("/"), api_key


def _gateway_headers(api_key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# asset 注册（magic 校验 + 原子写 + 版本图）
# ---------------------------------------------------------------------------

def _register_asset(d: Path, doc: Dict[str, Any], data: bytes, media_type: str, name: str) -> Dict[str, Any]:
    media_type = gs.validate_asset_bytes(data, media_type)
    aid = uuid.uuid4().hex[:16]
    d_assets = d / "assets"
    d_assets.mkdir(exist_ok=True)
    gs.atomic_write_bytes(gs.safe_join(d_assets, gs.asset_filename(media_type, aid)), data)
    width, height = gs.image_dimensions(data, media_type) if media_type.startswith("image/") else (0, 0)
    asset = {
        "id": aid, "name": name, "mediaType": media_type, "bytes": len(data),
        "width": width, "height": height, "createdAt": time.time(),
        "kind": "video" if media_type.startswith("video/") else "image",
    }
    doc["assets"][aid] = asset
    vid = uuid.uuid4().hex[:16]
    doc["versions"][vid] = {"id": vid, "assetId": aid, "createdAt": time.time(), "label": name}
    asset["versionId"] = vid
    return asset


# ---------------------------------------------------------------------------
# 图像生成（同步）
# ---------------------------------------------------------------------------

class ImageJobRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: Optional[str] = None
    size: Optional[str] = None
    n: int = 1


def _default_image_model() -> str:
    from hermes_cli.config import cfg_get, load_config

    return str(cfg_get(load_config(), "gugu_design", "image_model", default="qwen-image-2.0"))


def _fail_job(pid: str, job_id: str, message: str) -> None:
    with _project_lock(pid):
        try:
            d = _project_dir(pid)
            doc = _load_doc(d)
            job = doc["jobs"].get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = {"message": message[:400], "retryable": True}
                job["updatedAt"] = time.time()
                doc["revision"] += 1
                _save_doc(d, doc, kind="job-failed")
                _publish(pid, {"type": "job", "id": job_id, "status": "failed"})
        except HTTPException:
            pass


def _run_image_job(pid: str, job_id: str, req: Dict[str, Any]) -> None:
    try:
        base_url, api_key = _gateway()
    except HTTPException as exc:
        _fail_job(pid, job_id, str(exc.detail))
        return
    model = str(req.get("model") or _default_image_model())
    body: Dict[str, Any] = {"model": model, "prompt": str(req.get("prompt") or ""), "n": max(1, min(int(req.get("n") or 1), 4))}
    if req.get("size"):
        body["size"] = req["size"]
    try:
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(f"{base_url}/images/generations", headers=_gateway_headers(api_key), json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            items = resp.json().get("data") or []
            with _project_lock(pid):
                d = _project_dir(pid)
                doc = _load_doc(d)
                job = doc["jobs"][job_id]
                asset_ids: List[str] = []
                for i, item in enumerate(items):
                    b64 = item.get("b64_json")
                    url = item.get("url")
                    data = base64.b64decode(b64) if b64 else None
                    media_type = "image/png"
                    if data is None and url:
                        img = client.get(url, timeout=120.0)
                        img.raise_for_status()
                        data = img.content
                        media_type = img.headers.get("content-type", "image/png").split(";")[0]
                    if not data:
                        continue
                    asset = _register_asset(d, doc, data, media_type, f"{model}-{job_id[:6]}-{i}")
                    asset_ids.append(asset["id"])
                job["status"] = "succeeded" if asset_ids else "failed"
                job["outputAssetIds"] = asset_ids
                if not asset_ids:
                    job["error"] = {"message": "网关未返回图像", "retryable": True}
                job["updatedAt"] = time.time()
                doc["revision"] += 1
                _save_doc(d, doc, kind="job-succeeded")
                _publish(pid, {"type": "job", "id": job_id, "status": job["status"], "assets": asset_ids})
    except Exception as exc:  # noqa: BLE001 — 后台线程异常必须落进 job
        log.exception("gugu-design image job failed")
        _fail_job(pid, job_id, str(exc))


# ---------------------------------------------------------------------------
# 视频生成（异步任务 + 后台轮询）
# ---------------------------------------------------------------------------

class VideoJobRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: Optional[str] = None
    duration: int = 5
    aspect_ratio: Optional[str] = None
    media_asset_ids: List[str] = []


def _default_video_model() -> str:
    from hermes_cli.config import cfg_get, load_config

    return str(cfg_get(load_config(), "gugu_design", "video_model", default="wan3.0-video-prime"))


def _run_video_job(pid: str, job_id: str, req: Dict[str, Any]) -> None:
    try:
        base_url, api_key = _gateway()
    except HTTPException as exc:
        _fail_job(pid, job_id, str(exc.detail))
        return
    model = str(req.get("model") or _default_video_model())
    body: Dict[str, Any] = {"model": model, "prompt": str(req.get("prompt") or ""), "duration": str(req.get("duration") or 5)}
    if req.get("aspect_ratio"):
        body["aspect_ratio"] = req["aspect_ratio"]
    try:
        d = _project_dir(pid)
        with httpx.Client(timeout=60.0) as client:
            with _project_lock(pid):
                doc = _load_doc(d)
            data_urls = []
            for aid in list(req.get("media_asset_ids") or [])[:2]:
                asset = (doc.get("assets") or {}).get(aid) or {}
                ext = gs.asset_filename(str(asset.get("mediaType", "")), aid).rsplit(".", 1)[-1]
                raw = gs.safe_join(d / "assets", f"{aid}.{ext}").read_bytes()
                mime = str(asset.get("mediaType", "image/png"))
                data_urls.append(f"data:{mime};base64,{base64.b64encode(raw).decode()}")
            if len(data_urls) == 1:
                body["image"] = data_urls[0]
            elif len(data_urls) >= 2:
                body["first_frame_url"] = data_urls[0]
                body["last_frame_url"] = data_urls[1]
            resp = client.post(f"{base_url}/videos", headers=_gateway_headers(api_key), json=body)
            if resp.status_code not in (200, 201, 202):
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            task = resp.json()
            task_id = task.get("id", "")
            with _project_lock(pid):
                doc = _load_doc(d)
                job = doc["jobs"][job_id]
                job["upstreamTaskId"] = task_id
                job["status"] = "running"
                job["updatedAt"] = time.time()
                doc["revision"] += 1
                _save_doc(d, doc, kind="job-running")
                _publish(pid, {"type": "job", "id": job_id, "status": "running"})
            deadline = time.time() + _VIDEO_TIMEOUT_S
            status = "running"
            while time.time() < deadline:
                time.sleep(_VIDEO_POLL_S)
                st = client.get(f"{base_url}/videos/{task_id}", headers={"Authorization": f"Bearer {api_key}"})
                if st.status_code != 200:
                    continue
                payload = st.json()
                status = str(payload.get("status") or "running")
                progress = payload.get("progress")
                with _project_lock(pid):
                    doc = _load_doc(d)
                    doc["jobs"][job_id]["progress"] = progress
                    doc["revision"] += 1
                    _save_doc(d, doc, kind="job-progress")
                _publish(pid, {"type": "job", "id": job_id, "status": status, "progress": progress})
                if status in ("completed", "succeeded", "finished"):
                    break
                if status in ("failed", "cancelled", "expired"):
                    raise RuntimeError(f"上游任务 {status}")
            else:
                raise RuntimeError("视频任务轮询超时")
            content = client.get(f"{base_url}/videos/{task_id}/content", timeout=300.0)
            content.raise_for_status()
            with _project_lock(pid):
                doc = _load_doc(d)
                asset = _register_asset(d, doc, content.content, "video/mp4", f"{model}-{job_id[:6]}")
                job = doc["jobs"][job_id]
                job["status"] = "succeeded"
                job["outputAssetIds"] = [asset["id"]]
                job["progress"] = 100
                job["updatedAt"] = time.time()
                doc["revision"] += 1
                _save_doc(d, doc, kind="job-succeeded")
                _publish(pid, {"type": "job", "id": job_id, "status": "succeeded", "assets": [asset["id"]]})
    except Exception as exc:  # noqa: BLE001
        log.exception("gugu-design video job failed")
        _fail_job(pid, job_id, str(exc))


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get("/gateway")
async def gateway_status():
    try:
        base_url, _ = _gateway()
    except HTTPException:
        return {"configured": False}
    return {"configured": True, "base_url": base_url}


@router.get("/gateway-models")
async def gateway_models(modality: str = "image"):
    """代理网关按模态收口的模型清单（Design 面板模型下拉用）。"""
    if modality not in ("image", "video"):
        raise HTTPException(status_code=400, detail="modality 必须是 image 或 video")
    try:
        base_url, api_key = _gateway()
    except HTTPException:
        return {"models": []}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{base_url}/models", params={"modality": modality},
                              headers={"Authorization": f"Bearer {api_key}"})
            if resp.status_code != 200:
                return {"models": []}
            return {"models": [{"id": m.get("id", ""), "label": m.get("id", "")}
                               for m in resp.json().get("data", []) if m.get("id")]}
    except httpx.HTTPError:
        return {"models": []}


@router.get("/projects")
async def list_projects():
    root = _designs_root()
    out = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not _PROJECT_ID_RE.match(child.name):
            continue
        try:
            doc = _load_doc(child)
            out.append({
                "id": child.name, "name": doc.get("name", child.name),
                "updatedAt": doc.get("updatedAt", 0),
                "assets": len(doc.get("assets", {})),
            })
        except (HTTPException, json.JSONDecodeError):
            continue
    return {"projects": out}


class CreateProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.post("/projects")
async def create_project(body: CreateProjectBody):
    pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = _designs_root() / pid
    d.mkdir(parents=True)
    (d / "assets").mkdir()
    doc = _new_document(body.name.strip())
    doc["id"] = pid
    _save_doc(d, doc, kind="create")
    return {"id": pid, "document": doc}


class AgentStateBody(BaseModel):
    """渲染端上报的 agent 视角状态（surface/project/selection）。

    只做 ``read_state`` 的输入，不参与画布文档：单个 JSON 文件、最后写入者胜，
    无历史价值；写失败不该影响画布交互，前端吞掉错误。
    """

    surface: str = Field(default="unknown", max_length=32)
    route: str = Field(default="", max_length=200)
    project_id: Optional[str] = Field(default=None, max_length=64)
    selection: List[str] = Field(default_factory=list, max_length=64)


def _agent_state_path() -> Path:
    return _designs_root() / "agent-state.json"


@router.get("/agent-state")
async def get_agent_state():
    path = _agent_state_path()
    if not path.is_file():
        return {"state": None}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": None}
    return {"state": state if isinstance(state, dict) else None}


@router.put("/agent-state")
async def put_agent_state(body: AgentStateBody):
    state = {
        "surface": body.surface,
        "route": body.route,
        "project_id": body.project_id,
        "selection": list(body.selection),
        "updated_at": time.time(),
    }
    gs.atomic_write_json(_agent_state_path(), state)
    return {"ok": True, "state": state}


# ---- M2 guidance mailbox ---------------------------------------------------
# 后端还没有 renderer 方向的 host 通道（M1 剩余），所以 agent 侧把「当前唯一
# 一步引导」原子写到 designs/guidance.json，/design surface 轮询消费；渲染端
# 任何本地中断都会 DELETE 掉它，避免 agent 下一次读到陈旧 overlay。
# 文件名与 plugins/gugu_capabilities/store.py 保持一致。

def _guidance_path() -> Path:
    return _designs_root() / "guidance.json"


@router.get("/guidance")
async def get_guidance():
    path = _guidance_path()
    if not path.is_file():
        return {"guidance": None}
    try:
        guidance = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"guidance": None}
    return {"guidance": guidance if isinstance(guidance, dict) else None}


@router.delete("/guidance")
async def dismiss_guidance(guidance_id: Optional[str] = Query(default=None)):
    """Idempotent local dismiss. With ``guidance_id`` only delete that step, so a
    late DELETE from a previous step cannot erase the agent's new guidance."""
    path = _guidance_path()
    if not path.is_file():
        return {"cleared": False}
    if guidance_id:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"cleared": False}
        if not isinstance(current, dict) or str(current.get("guidance_id") or "") != guidance_id:
            return {"cleared": False}
    try:
        path.unlink()
    except FileNotFoundError:
        return {"cleared": False}
    return {"cleared": True}


@router.get("/projects/{pid}")
async def get_project(pid: str):
    d = _project_dir(pid)
    _recover_project_jobs(pid, d)
    with _project_lock(pid):
        doc = _load_doc(d)
    return {"id": pid, "document": doc}


class SaveDocumentBody(BaseModel):
    document: Dict[str, Any]
    expected_revision: int
    operation_id: str = ""


@router.put("/projects/{pid}/document")
async def save_document(pid: str, body: SaveDocumentBody):
    d = _project_dir(pid)
    _recover_project_jobs(pid, d)
    with _project_lock(pid):
        current = _load_doc(d)
        # 幂等重放：同 operation_id 直接返回当前文档（网络重试安全）。
        if gs.replay_operation(current, body.operation_id) is not None:
            return {"document": current}
        if body.expected_revision != current["revision"]:
            raise HTTPException(status_code=409, detail="文档版本冲突（他人已修改），已返回最新副本")
        # Host 权威合并（dsh save 语义）：客户端只拥有布局面（elements/viewport/name/comments），
        # jobs/assets/versions/幂等窗口由 Host 持有，整体替换会丢 Host 字段。
        # comments 自 M2b 起客户端可写（注释锚点）；送会话处理在 M4 走专用端点。
        doc = dict(current)
        for host_field in ("assets", "elements", "versions", "jobs", "comments"):
            doc.setdefault(host_field, {})
        for key in ("elements", "viewport", "name", "comments"):
            if key in body.document:
                doc[key] = body.document[key]
        doc["revision"] = current["revision"] + 1
        doc["id"] = pid
        doc["createdAt"] = current.get("createdAt", time.time())
        doc["updatedAt"] = time.time()
        doc["recent_operation_ids"] = current.get("recent_operation_ids", [])
        doc["operation_results"] = current.get("operation_results", {})
        # 幂等窗口只存摘要（存整个 doc 会自引用 → JSON 循环）；重放返回当前文档。
        if body.operation_id:
            gs.remember_operation(doc, body.operation_id, {"revision": doc["revision"]})
        _save_doc(d, doc, kind="save", operation_id=body.operation_id)
    return {"document": doc}


@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    import shutil

    d = _project_dir(pid)
    shutil.rmtree(d)
    _recovery_done.discard(pid)
    return {"ok": True}


@router.post("/projects/{pid}/assets")
async def upload_asset(pid: str, file: UploadFile = File(...)):
    d = _project_dir(pid)
    data = await file.read()
    if len(data) > gs.MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="素材超过 32MB")
    media = (file.content_type or "").split(";")[0]
    try:
        media = gs.validate_asset_bytes(data, media)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    with _project_lock(pid):
        doc = _load_doc(d)
        asset = _register_asset(d, doc, data, media, file.filename or "asset")
        doc["revision"] += 1
        _save_doc(d, doc, kind="asset-upload")
    return {"asset": asset}


@router.get("/projects/{pid}/assets/{aid}")
async def get_asset(pid: str, aid: str, b64: Optional[int] = None):
    if not _ASSET_ID_RE.match(aid):
        raise HTTPException(status_code=400, detail="非法 asset id")
    d = _project_dir(pid)
    with _project_lock(pid):
        doc = _load_doc(d)
    asset = doc.get("assets", {}).get(aid)
    if not asset:
        raise HTTPException(status_code=404, detail="素材不存在")
    f = gs.safe_join(d / "assets", gs.asset_filename(asset["mediaType"], aid))
    if not f.is_file():
        raise HTTPException(status_code=404, detail="素材文件缺失")
    if b64:
        # 桌面插件 rest 门通道：<img> 无法带 session token（见前端 fetchAssetUrl）
        return {"data": base64.b64encode(f.read_bytes()).decode(), "mediaType": asset["mediaType"]}
    return FileResponse(f, media_type=asset["mediaType"])


@router.post("/projects/{pid}/export")
async def export_canvas(pid: str):
    """M1 export bundle: one self-contained zip (project + assets + preview + manifest)."""
    _project_dir(pid)
    try:
        import importlib

        exporter = importlib.import_module("plugins.gugu_capabilities.export")
        result = exporter.export_project(pid)
    except Exception as exc:  # noqa: BLE001 — surface a 4xx, never a 500 HTML page
        raise HTTPException(status_code=400, detail=f"导出失败: {exc}") from exc
    payload = Path(result["path"]).read_bytes()
    return {
        **result,
        "data": base64.b64encode(payload).decode("ascii"),
    }


class JobBody(BaseModel):
    kind: str  # "image" | "video"
    request: Dict[str, Any]
    operation_id: str = ""


@router.post("/projects/{pid}/jobs")
async def submit_job(pid: str, body: JobBody):
    if body.kind not in ("image", "video"):
        raise HTTPException(status_code=400, detail="kind 必须是 image 或 video")
    if body.kind == "image":
        req = ImageJobRequest(**body.request).model_dump()
    else:
        req = VideoJobRequest(**body.request).model_dump()
    d = _project_dir(pid)
    with _project_lock(pid):
        doc = _load_doc(d)
        replay = gs.replay_operation(doc, body.operation_id)
        if replay is not None:
            return replay
        job_id = uuid.uuid4().hex[:16]
        job = {
            "id": job_id, "kind": body.kind, "request": req, "status": "queued",
            "createdAt": time.time(), "updatedAt": time.time(), "outputAssetIds": [],
        }
        doc["jobs"][job_id] = job
        doc["revision"] += 1
        result = {"jobId": job_id}
        if body.operation_id:
            gs.remember_operation(doc, body.operation_id, result)
        _save_doc(d, doc, kind="job-queued", operation_id=body.operation_id)
    _start_job(pid, job)
    return result


@router.get("/projects/{pid}/jobs")
async def list_jobs(pid: str):
    d = _project_dir(pid)
    with _project_lock(pid):
        doc = _load_doc(d)
    return {"jobs": list(doc.get("jobs", {}).values())}


@router.websocket("/events")
async def events(ws: WebSocket, pid: str = Query(default="")):
    """工程事件流（job/document 修订变更）。鉴权走 dashboard canonical WS gate。"""
    global _main_loop
    from hermes_cli.web_server_chat import _ws_auth_ok

    if not _ws_auth_ok(ws):
        await ws.close(code=4401)
        return
    if not pid or not _PROJECT_ID_RE.match(pid):
        await ws.close(code=4400)
        return
    if _main_loop is None:
        _main_loop = asyncio.get_running_loop()
    with _subscribers_guard:
        _subscribers.setdefault(pid, set()).add(ws)
    try:
        while True:
            await ws.receive_text()  # 客户端心跳；内容忽略
    except WebSocketDisconnect:
        pass
    finally:
        with _subscribers_guard:
            _subscribers.get(pid, set()).discard(ws)
