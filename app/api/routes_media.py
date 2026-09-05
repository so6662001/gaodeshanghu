"""宣传物料接口：海报 PNG 与视频号成片。

流程：运营在后台填好行情/现货数据 → 上传自己拍的素材（可选）→ 后台线程调 poster/ 下的
渲染器出片 → 页面轮询任务状态 → 下载 video.mp4 / cover.png / cover_share.png。

渲染依赖 Chrome + ffmpeg，属于 CPU 密集任务，用单独线程池限制并发，避免拖垮短信调度。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import require_token
from app.config import settings

ROOT = Path(__file__).resolve().parents[2]
POSTER_DIR = ROOT / "poster"
MEDIA_DIR = ROOT / "data" / "media"
sys.path.insert(0, str(POSTER_DIR))

router = APIRouter(prefix="/api/media", tags=["宣传物料"])

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="media")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()

ALLOWED_FILES = {"video.mp4", "cover.png", "cover_share.png", "poster.png"}
MAX_UPLOAD_MB = 200


def require_token_or_query(
    x_api_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> str:
    """<video>/<img> 标签带不了请求头，文件下载允许 ?token= 传令牌。"""
    return require_token(x_api_token or token)


def _job_dir(job_id: str) -> Path:
    return MEDIA_DIR / job_id


def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id].update(fields)


def _log(job_id: str, text: str) -> None:
    with _lock:
        _jobs[job_id]["logs"].append(f"{datetime.now():%H:%M:%S} {text}")


async def _save_upload(upload: UploadFile | None, target: Path, kinds: tuple[str, ...]) -> Path | None:
    if upload is None or not upload.filename:
        return None
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in kinds:
        raise HTTPException(400, f"不支持的文件类型 {suffix}，允许：{' '.join(kinds)}")
    target = target.with_suffix(suffix)
    size = 0
    with target.open("wb") as fh:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                fh.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, f"文件超过 {MAX_UPLOAD_MB}MB")
            fh.write(chunk)
    return target


def _run(job_id: str, kind: str, data: dict, theme: str, video: Path | None, bgm: Path | None) -> None:
    import render as poster_render  # noqa: WPS433  延迟导入，避免没装 Chrome 时影响主应用启动
    import video as poster_video

    out = _job_dir(job_id)
    _update(job_id, status="running")
    try:
        files: dict[str, str] = {}
        if kind == "poster":
            poster_render.render_png(data, out / "poster.png", theme=theme)
            files["poster"] = "poster.png"
            _log(job_id, "海报渲染完成")
        else:
            results = poster_video.generate(
                data, out, video=video, theme=theme, bgm=bgm, log=lambda text: _log(job_id, text)
            )
            files = {key: path.name for key, path in results.items()}
        _update(job_id, status="done", files=files, finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as exc:  # noqa: BLE001  任何渲染错误都要落到任务状态里给前端看
        _log(job_id, f"失败：{exc}")
        _update(job_id, status="failed", error=str(exc))
    finally:
        # 素材只在生成时需要，用完即删，商户上传的视频不长期落盘
        for path in (video, bgm):
            if path:
                path.unlink(missing_ok=True)
        shutil.rmtree(out / "scenes", ignore_errors=True)


@router.post("/jobs", dependencies=[Depends(require_token)])
async def create_job(
    kind: str = Form("video", pattern="^(video|poster)$"),
    theme: str = Form("steel"),
    data: str = Form(..., description="海报数据 JSON，字段同 poster/data.example.json"),
    video: UploadFile | None = File(None),
    bgm: UploadFile | None = File(None),
    channel_qr: UploadFile | None = File(None),
    qr: UploadFile | None = File(None),
    avatar: UploadFile | None = File(None),
) -> dict:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"data 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict) or not payload.get("prices"):
        raise HTTPException(400, "data 至少需要 prices 列表")

    job_id = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    out = _job_dir(job_id)
    out.mkdir(parents=True, exist_ok=True)

    video_path = await _save_upload(video, out / "src_video", (".mp4", ".mov", ".m4v", ".webm"))
    bgm_path = await _save_upload(bgm, out / "src_bgm", (".mp3", ".m4a", ".aac", ".wav"))
    images = (".png", ".jpg", ".jpeg", ".webp")
    for field, key in ((channel_qr, "channel_qr_image"), (qr, "qr_image"), (avatar, "avatar_image")):
        saved = await _save_upload(field, out / f"src_{key}", images)
        if saved:
            payload[key] = str(saved)

    with _lock:
        _jobs[job_id] = {
            "id": job_id, "kind": kind, "theme": theme, "status": "queued",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "has_video": video_path is not None, "logs": [], "files": {},
        }
    _executor.submit(_run, job_id, kind, payload, theme, video_path, bgm_path)
    return _jobs[job_id]


@router.get("/jobs", dependencies=[Depends(require_token)])
def list_jobs(limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
    return jobs[:limit]


@router.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在（服务重启后任务列表会清空，文件仍在 data/media 下）")
    return job


@router.get("/jobs/{job_id}/{filename}", dependencies=[Depends(require_token_or_query)])
def download(job_id: str, filename: str) -> FileResponse:
    if not re.fullmatch(r"[0-9]{14}-[0-9a-f]{6}", job_id) or filename not in ALLOWED_FILES:
        raise HTTPException(404, "文件不存在")
    path = _job_dir(job_id) / filename
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    media_type = "video/mp4" if filename.endswith(".mp4") else "image/png"
    return FileResponse(path, media_type=media_type, filename=f"{job_id}-{filename}")


@router.get("/example", dependencies=[Depends(require_token)])
def example_data() -> dict:
    """给前端表单一份可直接改的样例。"""
    data = json.loads((POSTER_DIR / "data.example.json").read_text(encoding="utf-8"))
    data["brand"] = data.get("brand") or settings.app_name
    return data


@router.get("/themes", dependencies=[Depends(require_token)])
def themes() -> list[dict]:
    import render as poster_render

    return [{"key": key, "desc": desc} for key, desc in poster_render.THEMES.items()]
