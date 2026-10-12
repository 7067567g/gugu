"""gugu 登录授权路由（深 fork 定制）。

端点：
- ``GET  /gugu/auth/page`` —— 自托管授权页。非 /api 前缀，因此不受 dashboard auth
  网关约束（与 SPA 静态页同形；桌面服务只绑 127.0.0.1）。页面内嵌会话 token，表单
  回投 ``/api/gugu/auth/complete``。
- ``POST /api/gugu/auth/complete`` —— token 保护；校验 key → 持久化 → 把
  ``web_routers.oauth`` 里对应的 device_code 会话翻转为 approved，桌面 onboarding
  的既有轮询即可感知。

协议细节见 ``hermes_cli/gugu_service.py`` 模块文档。
"""

from __future__ import annotations

import html
import json
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from hermes_cli import gugu_service
from hermes_cli.web_deps import late
from hermes_cli.web_routers.oauth import _oauth_sessions, _oauth_sessions_lock

router = APIRouter()
_require_token = late("_require_token")


class GuguCompleteBody(BaseModel):
    session_id: str
    api_key: str
    base_url: str = ""
    model: Optional[str] = None


def _live_gugu_session(sid: str) -> dict:
    """取出仍是 pending 的 gugu 会话；不存在/已过期/状态不对一律 4xx（会话内容不外泄）。"""
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(sid)
        if not sess or sess.get("provider") != "gugu":
            raise HTTPException(status_code=404, detail="授权会话不存在或已结束，请在咕咕应用中重新发起登录")
        if sess.get("cancelled"):
            raise HTTPException(status_code=400, detail="本次授权已在应用中取消")
        if sess.get("status") != "pending":
            raise HTTPException(status_code=400, detail=f"授权会话状态不可用：{sess.get('status')}")
        if time.time() > float(sess.get("expires_at") or 0):
            sess["status"] = "expired"
            raise HTTPException(status_code=400, detail="授权会话已过期，请在咕咕应用中重新发起登录")
        return sess


# _SESSION_TOKEN 是 web_server 模块级 str（late() 只代理可调用），经 web_deps._server 读现值。
def _read_session_token() -> str:
    from hermes_cli.web_deps import _server

    return str(getattr(_server(), "_SESSION_TOKEN", "") or "")


# 渲染走 token 替换（str.replace）而非 .format：CSS/JS 里有大量字面花括号。
# 占位符：@@SID_SHORT@@、@@BASE_URL@@、@@TOKEN_JSON@@、@@SID_JSON@@（值先做 html/json 转义）。
_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>咕咕 · 登录授权</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; font-family: ui-sans-serif, system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
         display:grid; place-items:center; min-height:100vh; background:#f6f6f7; color:#17181c; }
  @media (prefers-color-scheme: dark) { body { background:#101013; color:#e7e7ea; } }
  .card { width:min(440px, calc(100vw - 32px)); padding:28px; border-radius:16px;
          background:Canvas; border:1px solid color-mix(in srgb, currentColor 12%, transparent);
          box-shadow:0 12px 40px rgba(0,0,0,.08); }
  .brand { font-size:20px; font-weight:700; letter-spacing:.02em; margin:0 0 4px; }
  .sub { margin:0 0 20px; font-size:13px; opacity:.65; }
  label { display:block; font-size:12.5px; opacity:.75; margin:14px 0 6px; }
  input { width:100%; box-sizing:border-box; padding:10px 12px; border-radius:9px; font-size:14px;
          border:1px solid color-mix(in srgb, currentColor 20%, transparent); background:Field; color:FieldText; }
  button { margin-top:20px; width:100%; padding:11px; border:0; border-radius:9px; font-size:15px;
           font-weight:600; background:#2f6bff; color:#fff; cursor:pointer; }
  button:disabled { opacity:.55; cursor:default; }
  .msg { margin-top:16px; padding:12px 14px; border-radius:10px; font-size:13.5px; display:none; }
  .msg.err { display:block; background:#fee7e7; color:#8b1c1c; }
  .msg.ok { display:block; background:#e6f6ea; color:#155e2c; }
  @media (prefers-color-scheme: dark) { .msg.err { background:#3a1214; color:#ffb4b4; } .msg.ok { background:#0e2f1a; color:#8fe3ab; } }
  .foot { margin-top:18px; font-size:11.5px; opacity:.5; line-height:1.6; }
  .code { font-family: ui-monospace, monospace; font-size:12px; opacity:.6; margin-top:4px; }
</style>
</head>
<body>
  <form class="card" id="f" autocomplete="off">
    <h1 class="brand">咕咕 · 登录授权</h1>
    <p class="sub">授权成功后返回咕咕应用继续。<span class="code">会话 @@SID_SHORT@@</span></p>
    <label for="key">聚合网关 API Key</label>
    <input id="key" name="api_key" type="password" placeholder="sk-..." required autofocus>
    <label for="base">网关地址</label>
    <input id="base" name="base_url" type="text" value="@@BASE_URL@@">
    <button id="go" type="submit">验证并授权</button>
    <div class="msg" id="msg"></div>
    <div class="foot">本页面由咕咕客户端在本机启动（仅回环地址）。密钥只提交给本机客户端与上方网关地址，
    用于验证后保存在本地配置。</div>
  </form>
<script>
  const TOKEN = @@TOKEN_JSON@@;
  const form = document.getElementById('f');
  const msg = document.getElementById('msg');
  const go = document.getElementById('go');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    go.disabled = true; go.textContent = '验证中…';
    msg.className = 'msg';
    try {
      const resp = await fetch('/api/gugu/auth/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Hermes-Session-Token': TOKEN },
        body: JSON.stringify({
          session_id: @@SID_JSON@@,
          api_key: form.api_key.value.trim(),
          base_url: form.base_url.value.trim(),
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.ok) {
        msg.className = 'msg ok';
        const y = (data.balance_yuan === null || data.balance_yuan === undefined)
          ? '' : '，余额 ¥' + Number(data.balance_yuan).toFixed(2);
        const who = data.tenant ? ('「' + data.tenant + '」') : '';
        msg.textContent = '授权成功：' + who + ' 默认模型 ' + data.model + y + '。请返回咕咕应用。';
        form.querySelectorAll('input,button').forEach(el => el.disabled = true);
        return;
      }
      msg.className = 'msg err';
      msg.textContent = (data && data.detail) ? data.detail : ('授权失败（HTTP ' + resp.status + '）');
    } catch (err) {
      msg.className = 'msg err';
      msg.textContent = '无法连接本机咕咕客户端：' + err;
    } finally {
      go.disabled = false; go.textContent = '验证并授权';
    }
  });
</script>
</body>
</html>
"""


@router.get("/gugu/auth/page", response_class=HTMLResponse)
async def gugu_auth_page(request: Request, sid: str = "") -> HTMLResponse:
    # 页面按会话打开；会话失效时只回文案，不泄露其它状态。
    session_ok = bool(sid)
    if session_ok:
        try:
            _live_gugu_session(sid)
        except HTTPException:
            session_ok = False
    if not session_ok:
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>咕咕 · 登录授权</title>"
            "<body style='font-family:system-ui;display:grid;place-items:center;min-height:100vh'>"
            "<p>授权会话不存在或已过期，请在咕咕应用中重新发起登录。</p></body>",
            status_code=404,
        )
    token = _read_session_token()
    base_url = html.escape(gugu_service.gateway_default_base_url(), quote=True)
    page = (
        _PAGE.replace("@@SID_SHORT@@", html.escape(sid[:8]))
        .replace("@@BASE_URL@@", base_url)
        .replace("@@TOKEN_JSON@@", json.dumps(token))
        .replace("@@SID_JSON@@", json.dumps(sid))
    )
    return HTMLResponse(page)


@router.post("/api/gugu/auth/complete")
async def gugu_auth_complete(request: Request, body: GuguCompleteBody):
    _require_token(request)
    sess = _live_gugu_session(body.session_id)

    try:
        info = await _run_sync(gugu_service.validate_key, body.api_key, body.base_url)
        model = (body.model or "").strip() or gugu_service.pick_default_model(info["model_ids"])
        saved = await _run_sync(gugu_service.persist, body.api_key, info["base_url"], model)
    except ValueError as exc:
        # 会话保持 pending，让用户在页面上修正后重试；错误只回给页面，不进轮询状态。
        with _oauth_sessions_lock:
            sess["error_message"] = str(exc)
        raise HTTPException(status_code=400, detail=str(exc))

    balance = info.get("balance") or {}
    tenant = (balance.get("tenant") or {}).get("name") if isinstance(balance, dict) else None
    balance_yuan = balance.get("balance_yuan") if isinstance(balance, dict) else None
    with _oauth_sessions_lock:
        sess.update(
            {
                "status": "approved",
                "model": saved["model"],
                "base_url": saved["base_url"],
                "error_message": None,
                # poll 端点透传字段：桌面 onboarding 直接消费。
                "reason": "gugu_gateway_signed_in",
                "account_email": tenant,
            }
        )
    return {
        "ok": True,
        "model": saved["model"],
        "base_url": saved["base_url"],
        "tenant": tenant,
        "balance_yuan": balance_yuan,
    }


async def _run_sync(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)
