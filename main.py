"""
Telegram 群组管理后台 - FastAPI 服务
"""
import asyncio
import io
import uuid
import zipfile
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, Response
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError

from telegram_service import pull_active_users_to_group, get_active_users_from_group
import config

app = FastAPI(title="Telegram 群组管理后台")

Path(config.SESSIONS_DIR).mkdir(exist_ok=True)

# 登录流程中暂存的客户端（session_name -> {client, phone, phone_code_hash}）
_login_state: dict = {}
# 导出任务状态（job_id -> {progress, total, status, users, error}）
_export_jobs: dict = {}


def get_session_names() -> list[str]:
    """获取所有已登录的会话名称"""
    sessions = []
    for f in Path(config.SESSIONS_DIR).glob("*.session"):
        sessions.append(f.stem)
    return sorted(sessions)


def get_html(sessions: list[str]) -> str:
    session_options = "".join(f'<option value="{s}">{s}</option>' for s in sessions)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram 群组管理</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; margin: 0; padding: 24px; background: #0f0f12; color: #e4e4e7; min-height: 100vh; }}
        .container {{ max-width: 640px; margin: 0 auto; }}
        h1 {{ font-size: 1.5rem; margin-bottom: 24px; color: #fafafa; }}
        .card {{ background: #18181b; border-radius: 12px; padding: 24px; margin-bottom: 20px; border: 1px solid #27272a; }}
        .card h2 {{ font-size: 1rem; margin: 0 0 16px; color: #a1a1aa; font-weight: 600; }}
        label {{ display: block; margin-bottom: 6px; color: #a1a1aa; font-size: 0.875rem; }}
        input, select {{ width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #3f3f46; background: #27272a; color: #fafafa; font-size: 0.9375rem; margin-bottom: 12px; }}
        input:focus, select:focus {{ outline: none; border-color: #6366f1; }}
        button {{ background: #6366f1; color: white; border: none; padding: 10px 20px; border-radius: 8px; cursor: pointer; font-size: 0.9375rem; font-weight: 500; }}
        button:hover {{ background: #4f46e5; }}
        button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .result {{ margin-top: 16px; padding: 12px; border-radius: 8px; background: #27272a; font-size: 0.875rem; white-space: pre-wrap; max-height: 300px; overflow-y: auto; }}
        .success {{ color: #22c55e; }}
        .error {{ color: #ef4444; }}
        .hint {{ font-size: 0.75rem; color: #71717a; margin-top: 4px; }}
        .login-step {{ display: none; }}
        .login-step.active {{ display: block; }}
        .progress-bar {{ margin-top: 12px; height: 8px; background: #27272a; border-radius: 4px; overflow: hidden; }}
        .progress-fill {{ height: 100%; background: #6366f1; transition: width 0.2s; }}
        .progress-text {{ font-size: 0.8rem; color: #a1a1aa; margin-top: 6px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Telegram 群组管理后台</h1>

        <div class="card">
            <h2>1. 登录 Telegram 账号</h2>
            <p class="hint">请先登录账号，才能进行后续操作</p>
            <div id="login-step1" class="login-step active">
                <label>会话名称</label>
                <input type="text" id="login-session" placeholder="如: account1" maxlength="50">
                <label>手机号（含国家码）</label>
                <input type="text" id="login-phone" placeholder="如: +8613800138000">
                <button type="button" id="btn-send-code">发送验证码</button>
            </div>
            <div id="login-step2" class="login-step">
                <label>验证码</label>
                <input type="text" id="login-code" placeholder="请输入 Telegram 收到的验证码">
                <button type="button" id="btn-verify">验证</button>
            </div>
            <div id="login-step3" class="login-step">
                <label>两步验证密码</label>
                <input type="password" id="login-password" placeholder="请输入两步验证密码">
                <button type="button" id="btn-password">完成登录</button>
            </div>
            <div id="login-result" class="result" style="display:none;"></div>
        </div>

        <div class="card">
            <h2>2. 导出群 B 活人列表</h2>
            <p class="hint">获取群 B 中发送过消息的用户，导出用户名和 ID 到 txt 文件</p>
            <form id="form-export" onsubmit="submitExport(event)">
                <label>选择操作账号</label>
                <select name="session" id="session-export" required>
                    <option value="">请选择</option>
                    {session_options}
                </select>
                <label>群 B 链接</label>
                <input type="text" name="group_b" placeholder="https://t.me/xxx 或 https://t.me/joinchat/xxx" required>
                <button type="submit" id="btn-export">导出到 txt</button>
            </form>
            <div id="progress-export" style="display:none;">
                <div id="progress-message-count" class="progress-text" style="margin-bottom:4px;">已扫描 0 条消息</div>
                <div id="progress-earliest-date" class="progress-text" style="margin-bottom:4px;"></div>
                <div class="progress-bar"><div id="progress-fill" class="progress-fill" style="width:0%"></div></div>
                <div id="progress-text" class="progress-text">采集进度: 0/0</div>
            </div>
            <div id="result-export" class="result" style="display:none;"></div>
        </div>

        <div class="card">
            <h2>3. 从群 B 拉活人到群 A</h2>
            <p class="hint">活人 = 在群 B 中发送过消息的用户。操作账号需在群 A 有管理员权限。</p>
            <form id="form-pull" onsubmit="submitPull(event)">
                <label>选择操作账号</label>
                <select name="session" id="session-pull" required>
                    <option value="">请选择（请先登录账号）</option>
                    {session_options}
                </select>
                <label>群 B 链接（源群，从中获取活人）</label>
                <input type="text" name="group_b" placeholder="https://t.me/xxx 或 https://t.me/joinchat/xxx" required>
                <label>群 A 链接（目标群，活人将被拉入）</label>
                <input type="text" name="group_a" placeholder="https://t.me/xxx 或 https://t.me/joinchat/xxx" required>
                <button type="submit" id="btn-pull">开始拉人</button>
            </form>
            <div id="result-pull" class="result" style="display:none;"></div>
        </div>
    </div>
    <script>
        function formatEarliestDate(isoStr) {{
            try {{
                const d = new Date(isoStr);
                return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0') + ' ' + String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
            }} catch(e) {{ return isoStr; }}
        }}
        function showStep(stepId) {{
            document.querySelectorAll('.login-step').forEach(el => el.classList.remove('active'));
            const el = document.getElementById(stepId);
            if (el) el.classList.add('active');
        }}
        function showResult(msg, isError) {{
            const r = document.getElementById('login-result');
            r.style.display = 'block';
            r.textContent = msg;
            r.className = 'result ' + (isError ? 'error' : 'success');
        }}
        async function sendCode() {{
            const session = document.getElementById('login-session').value.trim();
            const phone = document.getElementById('login-phone').value.trim();
            if (!session || !phone) {{ alert('请填写会话名称和手机号'); return; }}
            const btn = document.getElementById('btn-send-code');
            btn.disabled = true;
            showResult('发送中...', false);
            try {{
                const r = await fetch('/api/login/send-code', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{session_name: session, phone: phone}}) }});
                const data = await r.json();
                if (data.error) {{ showResult(data.error, true); return; }}
                showResult('验证码已发送到你的 Telegram，请查收', false);
                showStep('login-step2');
            }} catch (e) {{ showResult('请求失败: ' + e.message, true); }}
            btn.disabled = false;
        }}
        async function verifyCode() {{
            const session = document.getElementById('login-session').value.trim();
            const code = document.getElementById('login-code').value.trim();
            if (!session || !code) {{ alert('请填写验证码'); return; }}
            const btn = document.getElementById('btn-verify');
            btn.disabled = true;
            showResult('验证中...', false);
            try {{
                const r = await fetch('/api/login/verify', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{session_name: session, code: code}}) }});
                const data = await r.json();
                if (data.error) {{ showResult(data.error, true); return; }}
                if (data.need_password) {{
                    showResult('请输入两步验证密码', false);
                    showStep('login-step3');
                }} else {{
                    showResult('登录成功！账号: ' + (data.name || session), false);
                    setTimeout(() => location.reload(), 1500);
                }}
            }} catch (e) {{ showResult('请求失败: ' + e.message, true); }}
            btn.disabled = false;
        }}
        async function submitPassword() {{
            const session = document.getElementById('login-session').value.trim();
            const password = document.getElementById('login-password').value;
            if (!session || !password) {{ alert('请填写密码'); return; }}
            const btn = document.getElementById('btn-password');
            btn.disabled = true;
            showResult('验证中...', false);
            try {{
                const r = await fetch('/api/login/password', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{session_name: session, password: password}}) }});
                const data = await r.json();
                if (data.error) {{ showResult(data.error, true); return; }}
                showResult('登录成功！账号: ' + (data.name || session), false);
                setTimeout(() => location.reload(), 1500);
            }} catch (e) {{ showResult('请求失败: ' + e.message, true); }}
            btn.disabled = false;
        }}
        document.getElementById('btn-send-code').onclick = sendCode;
        document.getElementById('btn-verify').onclick = verifyCode;
        document.getElementById('btn-password').onclick = submitPassword;

        async function submitExport(e) {{
            e.preventDefault();
            const form = document.getElementById('form-export');
            const btn = document.getElementById('btn-export');
            const result = document.getElementById('result-export');
            const progressDiv = document.getElementById('progress-export');
            const progressFill = document.getElementById('progress-fill');
            const progressText = document.getElementById('progress-text');
            btn.disabled = true;
            result.style.display = 'none';
            progressDiv.style.display = 'block';
            progressFill.style.width = '0%';
            document.getElementById('progress-message-count').textContent = '扫描消息中...';
            document.getElementById('progress-earliest-date').textContent = '';
            progressText.textContent = '';
            try {{
                const fd = new FormData(form);
                const r = await fetch('/api/export-active-users/start', {{ method: 'POST', body: fd }});
                const data = await r.json();
                if (data.error) {{
                    result.style.display = 'block';
                    result.textContent = data.error;
                    result.className = 'result error';
                    progressDiv.style.display = 'none';
                    btn.disabled = false;
                    return;
                }}
                const jobId = data.job_id;
                const poll = async () => {{
                    const sr = await fetch('/api/export-active-users/status/' + jobId);
                    const s = await sr.json();
                    if (s.status === 'complete') {{
                        progressFill.style.width = '100%';
                        document.getElementById('progress-message-count').textContent = '已扫描 ' + (s.message_count || 0) + ' 条消息';
                        document.getElementById('progress-earliest-date').textContent = s.earliest_date ? ('最早消息日期: ' + formatEarliestDate(s.earliest_date)) : '';
                        progressText.textContent = '采集完成，正在下载 zip（含活人列表+扫描消息）...';
                        const dr = await fetch('/api/export-active-users/download/' + jobId);
                        const blob = await dr.blob();
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = dr.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1] || 'active_users.txt';
                        a.click();
                        URL.revokeObjectURL(url);
                        progressDiv.style.display = 'none';
                        result.style.display = 'block';
                        result.textContent = '导出成功，已下载 zip（含 active_users.txt 和 scanned_messages.txt）';
                        result.className = 'result success';
                        btn.disabled = false;
                        return;
                    }}
                    if (s.status === 'error') {{
                        progressDiv.style.display = 'none';
                        result.style.display = 'block';
                        result.textContent = s.error || '导出失败';
                        result.className = 'result error';
                        btn.disabled = false;
                        return;
                    }}
                    const pct = s.total ? Math.round(100 * s.processed / s.total) : 0;
                    progressFill.style.width = pct + '%';
                    document.getElementById('progress-message-count').textContent = '已扫描 ' + (s.message_count || 0) + ' 条消息';
                    document.getElementById('progress-earliest-date').textContent = s.earliest_date ? ('最早消息日期: ' + formatEarliestDate(s.earliest_date)) : '';
                    progressText.textContent = s.total ? ('已采集 ' + s.progress + ' 个有用户名用户 (检查 ' + s.processed + '/' + s.total + ')') : '扫描消息中...';
                    setTimeout(poll, 500);
                }};
                await poll();
            }} catch (err) {{
                progressDiv.style.display = 'none';
                result.style.display = 'block';
                result.textContent = '请求失败: ' + err.message;
                result.className = 'result error';
                btn.disabled = false;
            }}
        }}

        async function submitPull(e) {{
            e.preventDefault();
            const form = e.target;
            const btn = document.getElementById('btn-pull');
            const result = document.getElementById('result-pull');
            btn.disabled = true;
            result.style.display = 'block';
            result.textContent = '处理中，请耐心等待...';
            result.className = 'result';
            try {{
                const fd = new FormData(form);
                const r = await fetch('/api/pull-active-users', {{ method: 'POST', body: fd }});
                const data = await r.json();
                result.textContent = JSON.stringify(data, null, 2);
                result.className = 'result ' + (data.error ? 'error' : 'success');
            }} catch (err) {{
                result.textContent = '请求失败: ' + err.message;
                result.className = 'result error';
            }}
            btn.disabled = false;
        }}
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    sessions = get_session_names()
    return get_html(sessions)


# ========== 登录 API ==========

@app.post("/api/login/send-code")
async def api_login_send_code(data: dict):
    session_name = (data.get("session_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    if not session_name or not phone:
        return {"error": "请填写会话名称和手机号"}
    if config.API_ID == 0 or not config.API_HASH:
        return {"error": "请先在 config.py 中配置 API_ID 和 API_HASH"}
    # 清理该会话的旧状态
    if session_name in _login_state:
        try:
            await _login_state[session_name]["client"].disconnect()
        except Exception:
            pass
        del _login_state[session_name]
    try:
        session_path = Path(config.SESSIONS_DIR) / session_name
        client = TelegramClient(str(session_path), config.API_ID, config.API_HASH)
        await client.connect()
        result = await client.send_code_request(phone)
        _login_state[session_name] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": result.phone_code_hash,
        }
        return {"ok": True}
    except FloodWaitError as e:
        return {"error": f"请求过于频繁，请等待 {e.seconds} 秒后重试"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/login/verify")
async def api_login_verify(data: dict):
    session_name = (data.get("session_name") or "").strip()
    code = (data.get("code") or "").strip()
    if not session_name or not code:
        return {"error": "请填写验证码"}
    state = _login_state.get(session_name)
    if not state:
        return {"error": "会话已过期，请重新发送验证码"}
    try:
        await state["client"].sign_in(
            phone=state["phone"],
            code=code,
            phone_code_hash=state["phone_code_hash"],
        )
        me = await state["client"].get_me()
        await state["client"].disconnect()
        del _login_state[session_name]
        name = f"{me.first_name or ''} (@{me.username or '无'})".strip() or session_name
        return {"ok": True, "name": name}
    except SessionPasswordNeededError:
        return {"ok": True, "need_password": True}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/login/password")
async def api_login_password(data: dict):
    session_name = (data.get("session_name") or "").strip()
    password = data.get("password") or ""
    if not session_name:
        return {"error": "会话已过期"}
    state = _login_state.get(session_name)
    if not state:
        return {"error": "会话已过期，请重新发送验证码"}
    try:
        await state["client"].sign_in(password=password)
        me = await state["client"].get_me()
        await state["client"].disconnect()
        del _login_state[session_name]
        name = f"{me.first_name or ''} (@{me.username or '无'})".strip() or session_name
        return {"ok": True, "name": name}
    except Exception as e:
        return {"error": str(e)}


# ========== 导出活人 API ==========

@app.post("/api/export-active-users/start")
async def api_export_start(
    session: str = Form(...),
    group_b: str = Form(...),
):
    """启动导出任务，返回 job_id"""
    job_id = str(uuid.uuid4())
    _export_jobs[job_id] = {"progress": 0, "total": 0, "processed": 0, "message_count": 0, "earliest_date": None, "status": "running", "users": [], "messages": [], "error": None}

    async def run_export():
        try:
            def on_progress(collected: int, processed: int, total: int, message_count: int | None = None, earliest_date=None):
                _export_jobs[job_id]["progress"] = collected  # 有用户名的数量
                _export_jobs[job_id]["processed"] = processed
                _export_jobs[job_id]["total"] = total
                if message_count is not None:
                    _export_jobs[job_id]["message_count"] = message_count
                if earliest_date is not None:
                    _export_jobs[job_id]["earliest_date"] = earliest_date.isoformat() if hasattr(earliest_date, 'isoformat') else str(earliest_date)

            result = await get_active_users_from_group(
                session_name=session,
                group_link=group_b,
                progress_callback=on_progress,
            )
            if not result.get("success"):
                _export_jobs[job_id]["status"] = "error"
                _export_jobs[job_id]["error"] = result.get("message", "获取失败")
                return
            _export_jobs[job_id]["users"] = result.get("users", [])
            _export_jobs[job_id]["messages"] = result.get("messages", [])
            _export_jobs[job_id]["status"] = "complete"
        except Exception as e:
            _export_jobs[job_id]["status"] = "error"
            _export_jobs[job_id]["error"] = str(e)

    asyncio.create_task(run_export())
    return {"job_id": job_id}


@app.get("/api/export-active-users/status/{job_id}")
async def api_export_status(job_id: str):
    job = _export_jobs.get(job_id)
    if not job:
        return {"status": "error", "error": "任务不存在"}
    return {
        "status": job["status"],
        "progress": job["progress"],
        "processed": job.get("processed", 0),
        "total": job["total"],
        "message_count": job.get("message_count", 0),
        "earliest_date": job.get("earliest_date"),
        "error": job.get("error"),
    }


@app.get("/api/export-active-users/download/{job_id}")
async def api_export_download(job_id: str):
    job = _export_jobs.get(job_id)
    if not job or job["status"] != "complete":
        raise HTTPException(404, "文件未就绪")
    users = job.get("users", [])
    messages = job.get("messages", [])
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 生成 active_users.txt
    user_lines = ["# ID\t用户名\t名\t姓", "# " + "=" * 50]
    for u in users:
        uid = u.get("id", "")
        raw_username = (u.get("username") or "").strip()
        username = f"@{raw_username}" if raw_username else "-"
        first_name = (u.get("first_name") or "").strip() or "-"
        last_name = (u.get("last_name") or "").strip() or "-"
        user_lines.append(f"{uid}\t{username}\t{first_name}\t{last_name}")
    user_content = "\n".join(user_lines).encode("utf-8")

    # 生成 scanned_messages.txt
    msg_lines = ["# 日期\t发送者ID\t消息内容", "# " + "=" * 50]
    for m in messages:
        date = m.get("date", "")
        sender_id = m.get("sender_id", "")
        text = (m.get("text", "") or "").replace("\n", " ").replace("\r", "")
        msg_lines.append(f"{date}\t{sender_id}\t{text}")
    msg_content = "\n".join(msg_lines).encode("utf-8")

    # 打包为 zip
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"active_users_{ts}.txt", user_content)
        zf.writestr(f"scanned_messages_{ts}.txt", msg_content)

    zip_buffer.seek(0)
    zip_filename = f"export_{ts}.zip"
    del _export_jobs[job_id]
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


# ========== 拉人 API ==========

@app.post("/api/pull-active-users")
async def api_pull_active_users(
    session: str = Form(...),
    group_b: str = Form(...),
    group_a: str = Form(...),
):
    try:
        result = await pull_active_users_to_group(
            session_name=session,
            source_group_link=group_b,
            target_group_link=group_a,
        )
        return result
    except Exception as e:
        return {"error": str(e), "success": False}


@app.get("/api/sessions")
async def api_sessions():
    return {"sessions": get_session_names()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
