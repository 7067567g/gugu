"""gugu-design 后端 —— 无限画布创作工作台 API，挂载于 /api/plugins/gugu-design/。

设计（对标万镜一刻画布，见 docs/01-Design功能对标清单-v1.md）：
- 工程 = ``HERMES_HOME/designs/<id>/``：``project.json``（CanvasDocument，含
  assets/elements/versions/jobs + revision 乐观锁）与 ``assets/`` 二进制。
- 生成全部经聚合网关（与桌面登录同一份 model 配置，见 hermes_cli.gugu_service）：
  图像 ``POST {base}/images/generations``（同步，b64/url 双形态）；
  视频 ``POST {base}/videos`` 异步任务 + 后台线程轮询 ``GET {base}/videos/{id}``，
  completed 后下载 ``/content`` 落盘。浏览器从不直连 provider（Host 排队，学 dsh-design-mode）。
- job 成功后产物注册为 asset + version；画布元素由前端放置（job 返回 assetIds）。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter()

_LOCK = threading.Lock()
_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ASSET_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4", "image/gif": "gif"}
_VIDEO_POLL_S = 10.0
_VIDEO_TIMEOUT_S = 25 * 60.0


# ---------------------------------------------------------------------------
# 存储层
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


def _doc_file(d: Path) -> Path:
    return d / "project.json"


def _load_doc(d: Path) -> Dict[str, Any]:
    f = _doc_file(d)
    if not f.exists():
        raise HTTPException(status_code=404, detail="工程文档缺失")
    return json.loads(f.read_text(encoding="utf-8"))


def _save_doc(d: Path, doc: Dict[str, Any]) -> None:
    _doc_file(d).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


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
        "elements": {},
        "versions": {},
        "jobs": {},
    }


# ---------------------------------------------------------------------------
# 网关凭据（与桌面登录同源：model.base_url + api_key/${ENV}）
# ---------------------------------------------------------------------------

def _gateway() -> tuple[str, str]:
    from hermes_cli.config import get_env_value, load_config
    from hermes_cli.model_setup_flows_common import _load_config_model_section

    _cfg, model = _load_config_model_section()
    base_url = str(model.get("base_url") or "").rstrip("/")
    key_ref = str(model.get("api_key") or "")
    api_key = ""
    if key_ref.startswith("${") and key_ref.endswith("}"):
        api_key = get_env_value(key_ref[2:-1]) or ""
    else:
        api_key = key_ref or get_env_value("OPENAI_API_KEY") or ""
    if not base_url or not api_key:
        raise HTTPException(status_code=400, detail="尚未登录咕咕聚合网关（请先在设置中完成网页授权）")
    return base_url, api_key


def _gateway_headers(api_key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# asset 注册
# ---------------------------------------------------------------------------

def _register_asset(d: Path, doc: Dict[str, Any], data: bytes, media_type: str, name: str) -> Dict[str, Any]:
    aid = uuid.uuid4().hex[:16]
    ext = _ASSET_EXT.get(media_type, "bin")
    (d / "assets").mkdir(exist_ok=True)
    (d / "assets" / f"{aid}.{ext}").write_bytes(data)
    width = height = 0
    if media_type.startswith("image/"):
        try:
            from PIL import Image

            with Image.open(d / "assets" / f"{aid}.{ext}") as im:
                width, height = im.size
        except Exception:  # noqa: BLE001 — 尺寸探测失败不阻断入库
            pass
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
    size: Optional[str] = None  # 例 "1024x1024"
    n: int = 1


def _default_image_model() -> str:
    from hermes_cli.config import cfg_get, load_config

    return str(cfg_get(load_config(), "gugu_design", "image_model", default="qwen-image-2.0"))


def _run_image_job(pid: str, job_id: str, req: ImageJobRequest) -> None:
    base_url, api_key = _gateway()
    model = req.model or _default_image_model()
    body: Dict[str, Any] = {"model": model, "prompt": req.prompt, "n": max(1, min(req.n, 4))}
    if req.size:
        body["size"] = req.size
    try:
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(f"{base_url}/images/generations", headers=_gateway_headers(api_key), json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            items = resp.json().get("data") or []
            with _LOCK:
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
                _save_doc(d, doc)
    except Exception as exc:  # noqa: BLE001 — 后台线程，异常必须落进 job
        log.exception("gugu-design image job failed")
        with _LOCK:
            try:
                d = _project_dir(pid)
                doc = _load_doc(d)
                job = doc["jobs"][job_id]
                job["status"] = "failed"
                job["error"] = {"message": str(exc)[:400], "retryable": True}
                job["updatedAt"] = time.time()
                doc["revision"] += 1
                _save_doc(d, doc)
            except HTTPException:
                pass


# ---------------------------------------------------------------------------
# 视频生成（异步任务 + 后台轮询）
# ---------------------------------------------------------------------------

class VideoJobRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: Optional[str] = None
    duration: int = 5
    aspect_ratio: Optional[str] = None
    media_asset_ids: List[str] = []  # 图生视频/首尾帧/参考


def _default_video_model() -> str:
    from hermes_cli.config import cfg_get, load_config

    return str(cfg_get(load_config(), "gugu_design", "video_model", default="wan3.0-video-prime"))


def _run_video_job(pid: str, job_id: str, req: VideoJobRequest) -> None:
    base_url, api_key = _gateway()
    model = req.model or _default_video_model()
    body: Dict[str, Any] = {"model": model, "prompt": req.prompt, "duration": str(req.duration)}
    if req.aspect_ratio:
        body["aspect_ratio"] = req.aspect_ratio
    try:
        d = _project_dir(pid)
        with httpx.Client(timeout=60.0) as client:
            # 参考图以 data URL 随任务提交（网关→厂商透传；上游不支持时错误会落进 job）。
            with _LOCK:
                doc = _load_doc(d)
            data_urls = []
            for aid in req.media_asset_ids[:2]:
                ext = _guess_ext(d, doc, aid)
                raw = (d / "assets" / f"{aid}.{ext}").read_bytes()
                mime = "image/png" if ext == "png" else "image/jpeg"
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
            with _LOCK:
                doc = _load_doc(d)
                job = doc["jobs"][job_id]
                job["upstreamTaskId"] = task_id
                job["status"] = "running"
                job["updatedAt"] = time.time()
                doc["revision"] += 1
                _save_doc(d, doc)
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
                with _LOCK:
                    doc = _load_doc(d)
                    job = doc["jobs"][job_id]
                    job["progress"] = progress
                    doc["revision"] += 1
                    _save_doc(d, doc)
                if status in ("completed", "succeeded", "finished"):
                    break
                if status in ("failed", "cancelled", "expired"):
                    raise RuntimeError(f"上游任务 {status}")
            else:
                raise RuntimeError("视频任务轮询超时")
            content = client.get(f"{base_url}/videos/{task_id}/content", timeout=300.0)
            content.raise_for_status()
            with _LOCK:
                doc = _load_doc(d)
                asset = _register_asset(d, doc, content.content, "video/mp4", f"{model}-{job_id[:6]}")
                job = doc["jobs"][job_id]
                job["status"] = "succeeded"
                job["outputAssetIds"] = [asset["id"]]
                job["updatedAt"] = time.time()
                doc["revision"] += 1
                _save_doc(d, doc)
    except Exception as exc:  # noqa: BLE001
        log.exception("gugu-design video job failed")
        with _LOCK:
            try:
                doc = _load_doc(d)
                job = doc["jobs"][job_id]
                job["status"] = "failed"
                job["error"] = {"message": str(exc)[:400], "retryable": True}
                job["updatedAt"] = time.time()
                doc["revision"] += 1
                _save_doc(d, doc)
            except Exception:  # noqa: BLE001
                pass


def _guess_ext(d: Path, doc: Dict[str, Any], aid: str) -> str:
    media = str((doc.get("assets", {}).get(aid) or {}).get("mediaType", ""))
    return _ASSET_EXT.get(media, "bin")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get("/gateway")
async def gateway_status():
    from fastapi import HTTPException as _H

    try:
        base_url, _ = _gateway()
    except _H:
        return {"configured": False}
    return {"configured": True, "base_url": base_url}


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
        except HTTPException:
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
    _save_doc(d, doc)
    return {"id": pid, "document": doc}


@router.get("/projects/{pid}")
async def get_project(pid: str):
    d = _project_dir(pid)
    with _LOCK:
        doc = _load_doc(d)
    return {"id": pid, "document": doc}


class SaveDocumentBody(BaseModel):
    document: Dict[str, Any]
    expected_revision: int


@router.put("/projects/{pid}/document")
async def save_document(pid: str, body: SaveDocumentBody):
    d = _project_dir(pid)
    with _LOCK:
        current = _load_doc(d)
        if body.expected_revision != current["revision"]:
            raise HTTPException(status_code=409, detail="文档版本冲突（他人已修改），已返回最新副本")
        doc = body.document
        doc["revision"] = current["revision"] + 1
        doc["id"] = pid
        doc["createdAt"] = current.get("createdAt", time.time())
        doc["updatedAt"] = time.time()
        _save_doc(d, doc)
    return {"document": doc}


@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    import shutil

    d = _project_dir(pid)
    shutil.rmtree(d)
    return {"ok": True}


@router.post("/projects/{pid}/assets")
async def upload_asset(pid: str, file: UploadFile = File(...)):
    d = _project_dir(pid)
    data = await file.read()
    if len(data) > 64 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="素材超过 64MB")
    media = (file.content_type or "image/png").split(";")[0]
    if media not in _ASSET_EXT:
        raise HTTPException(status_code=415, detail=f"不支持的素材类型 {media}")
    with _LOCK:
        doc = _load_doc(d)
        asset = _register_asset(d, doc, data, media, file.filename or asset_name_guess(media))
        doc["revision"] += 1
        _save_doc(d, doc)
    return {"asset": asset}


def asset_name_guess(media: str) -> str:
    return media.rsplit("/", 1)[-1] + ".bin"


@router.get("/projects/{pid}/assets/{aid}")
async def get_asset(pid: str, aid: str, b64: Optional[int] = None):
    if not re.match(r"^[a-f0-9]{16}$", aid):
        raise HTTPException(status_code=400, detail="非法 asset id")
    d = _project_dir(pid)
    with _LOCK:
        doc = _load_doc(d)
    asset = doc.get("assets", {}).get(aid)
    if not asset:
        raise HTTPException(status_code=404, detail="素材不存在")
    ext = _ASSET_EXT.get(asset["mediaType"], "bin")
    f = d / "assets" / f"{aid}.{ext}"
    if not f.exists():
        raise HTTPException(status_code=404, detail="素材文件缺失")
    if b64:
        # 桌面插件 rest 门通道（<img> 无法带 session token，见前端 fetchAssetUrl）
        return {"data": base64.b64encode(f.read_bytes()).decode(), "mediaType": asset["mediaType"]}
    return FileResponse(f, media_type=asset["mediaType"])


class JobBody(BaseModel):
    kind: str  # "image" | "video"
    request: Dict[str, Any]


@router.post("/projects/{pid}/jobs")
async def submit_job(pid: str, body: JobBody):
    d = _project_dir(pid)
    if body.kind == "image":
        req = ImageJobRequest(**body.request)
    elif body.kind == "video":
        req = VideoJobRequest(**body.request)
    else:
        raise HTTPException(status_code=400, detail="kind 必须是 image 或 video")
    job_id = uuid.uuid4().hex[:16]
    with _LOCK:
        doc = _load_doc(d)
        doc["jobs"][job_id] = {
            "id": job_id, "kind": body.kind, "request": body.request, "status": "queued",
            "createdAt": time.time(), "updatedAt": time.time(), "outputAssetIds": [],
        }
        doc["revision"] += 1
        _save_doc(d, doc)
    runner = _run_image_job if body.kind == "image" else _run_video_job
    threading.Thread(target=runner, args=(pid, job_id, req), daemon=True, name=f"gugu-design-{job_id[:6]}").start()
    return {"jobId": job_id}


@router.get("/projects/{pid}/jobs")
async def list_jobs(pid: str):
    d = _project_dir(pid)
    with _LOCK:
        doc = _load_doc(d)
    return {"jobs": list(doc.get("jobs", {}).values())}
