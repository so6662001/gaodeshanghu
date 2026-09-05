"""行情早报视频生成器。

用法：
    python poster/video.py poster/data.example.json --video 我的素材.mp4 --out out/
    python poster/video.py poster/data.example.json --out out/          # 没有素材时用动态渐变底

产出（都在 --out 目录）：
    video.mp4         1080×1920 竖版成片，直接上传视频号
    cover.png         视频号封面图（无播放键，视频号自己会加）
    cover_share.png   群内分享用封面：带播放键 + 视频号二维码角标，长按识别直达视频号
    scenes/*.png      五个场景的透明图层，需要二次剪辑时可用

机制说明：
    微信里「点视频直接进视频号」只有一条路 —— 视频发到视频号，再把视频号卡片分享进群。
    所以这里产出的是一条能直接上传视频号的成片，加片尾二维码兜底（视频被转存到别处时
    仍然能扫码回到视频号）。用户上传的素材只做背景，会被压暗，信息图层永远清晰。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import render as poster  # noqa: E402  复用卡片/资源行的 HTML 生成与 Chrome 查找

TEMPLATE = HERE / "video_template.html"
W, H = 1080, 1920
FPS = 30

# 每个主题的强调色，视频图层只换这个
THEME_ACCENT = {
    "steel": ("#ff7a1a", "#ffb27a"),
    "paper": ("#ff6a00", "#ffb27a"),
    "terminal": ("#f5b301", "#f5b301"),
    "industrial": ("#ff6a00", "#ffb27a"),
    "kraft": ("#d9543a", "#f0a58a"),
    "sky": ("#1a7ff0", "#8fc3ff"),
}

# 场景时间轴（秒）。节奏原则：封面停够 3 秒让人读懂结论，行情给最长时间，结尾留足扫码时间
TIMELINE = [
    ("cover", 0.0, 3.5),
    ("market", 3.5, 10.5),
    ("insight", 10.5, 14.5),
    ("resources", 14.5, 22.0),
    ("outro", 22.0, 27.0),
]
DURATION = TIMELINE[-1][2]
FADE = 0.6


# ---------------------------------------------------------------------------
# 图层渲染
# ---------------------------------------------------------------------------
def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def _trend_word(prices: list[dict]) -> str:
    ups = sum(1 for p in prices if int(p.get("change", 0)) > 0)
    downs = sum(1 for p in prices if int(p.get("change", 0)) < 0)
    if ups and not downs:
        return "全线上涨"
    if downs and not ups:
        return "全线回落"
    if ups > downs:
        return "整体偏强"
    if downs > ups:
        return "整体偏弱"
    return "涨跌互现"


def _kpis(prices: list[dict], limit: int = 3) -> str:
    movers = sorted(prices, key=lambda p: abs(int(p.get("change", 0))), reverse=True)[:limit]
    html = []
    for p in movers:
        change = int(p.get("change", 0))
        cls = "up" if change > 0 else "down" if change < 0 else "flat"
        text = f"▲ +{change}" if change > 0 else f"▼ {change}" if change < 0 else "— 持平"
        html.append(
            f'<div class="kpi"><div class="k">{escape(str(p["name"]))}</div>'
            f'<div class="v num">{int(p["price"]):,}</div><div class="c {cls}">{text}</div></div>'
        )
    return "".join(html)


def build_scene_html(data: dict, scene: str, theme: str, play: bool = False) -> str:
    accent, accent_soft = THEME_ACCENT.get(theme, THEME_ACCENT["steel"])
    prices = data.get("prices", [])
    market = {p["name"]: int(p["price"]) for p in prices}

    channel_qr = poster._img_tag(data.get("channel_qr_image") or data.get("qr_image", "")) or (
        '<div style="width:100%;height:100%;background:'
        "repeating-conic-gradient(#111 0 25%, #fff 0 50%) 0 0/24px 24px;"
        'border-radius:8px"></div>'
    )
    avatar = poster._img_tag(data.get("avatar_image", "")) or escape(data.get("avatar_text", "普"))

    values = {
        "accent": accent,
        "accent_soft": accent_soft,
        "cards": "\n".join(poster._card(p) for p in prices[:6]),
        "resources": "\n".join(poster._resource(r, market) for r in data.get("resources", [])[:4]),
        "kpis": _kpis(prices),
        "trend_word": _trend_word(prices),
        "summary_plain": escape(_strip_tags(data.get("summary", ""))),
        "channel_qr": channel_qr,
        "avatar": avatar,
        "cover_attrs": "data-play" if play else "",
        "channel_name": escape(str(data.get("channel_name", data.get("brand", "")))),
    }
    raw_fields = {"headline", "summary", "insight"}
    skip = {"prices", "resources", "avatar_image", "avatar_text", "qr_image", "channel_qr_image", "theme"}
    for key, value in data.items():
        if key in skip or key in values:
            continue
        values[key] = str(value) if key in raw_fields else escape(str(value))

    html = TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        html = html.replace(f"{{{{{key}}}}}", value)
    # 清掉未提供的占位符，避免残留 {{xxx}}
    html = re.sub(r"\{\{[a-z_]+\}\}", "", html)
    # 激活目标场景
    html = html.replace(f'data-scene="{scene}"', f'data-scene="{scene}" data-active')
    return html


def render_scene_png(data: dict, scene: str, theme: str, output: Path, play: bool = False) -> Path:
    html = build_scene_html(data, scene, theme, play)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        html_path = Path(tmp) / f"{scene}.html"
        html_path.write_text(html, encoding="utf-8")
        cmd = [
            poster.find_chrome(),
            "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
            f"--user-data-dir={tmp}/profile", "--hide-scrollbars",
            "--default-background-color=00000000",  # 透明底，叠到视频上
            "--force-device-scale-factor=1",
            f"--window-size={W},{H}",
            f"--screenshot={output.resolve()}",
            html_path.as_uri(),
        ]
        output.unlink(missing_ok=True)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline, last = time.time() + 40, -1
        try:
            while time.time() < deadline:
                if output.exists():
                    size = output.stat().st_size
                    if size and size == last:
                        break
                    last = size
                time.sleep(0.3)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
    if not output.exists():
        raise SystemExit(f"场景 {scene} 渲染失败")
    return output


# ---------------------------------------------------------------------------
# ffmpeg 合成
# ---------------------------------------------------------------------------
def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit("未找到 ffmpeg，请先安装：apt install ffmpeg / brew install ffmpeg")
    return path


def _bg_input(video: Path | None, theme: str) -> list[str]:
    """背景输入：用户素材循环播放；没有素材时用随主题色的动态渐变。"""
    if video:
        return ["-stream_loop", "-1", "-i", str(video)]
    accent, _ = THEME_ACCENT.get(theme, THEME_ACCENT["steel"])
    gradient = (
        f"gradients=s={W}x{H}:d={DURATION}:speed=0.012:nb_colors=4:"
        f"c0=#0b1220:c1=#182a52:c2={accent}:c3=#0b1220"
    )
    return ["-f", "lavfi", "-i", gradient]


BG_FILTER = (
    f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},"
    # 压暗 + 降饱和 + 暗角：让用户随手拍的素材都能当背景，信息图层永远看得清
    "eq=brightness=-0.16:saturation=0.8,vignette=PI/4.5,format=yuv420p"
)


def compose_video(bg_video: Path | None, scenes: dict[str, Path], output: Path,
                  theme: str, bgm: Path | None = None) -> Path:
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error"]
    cmd += _bg_input(bg_video, theme)
    for name, _start, _end in TIMELINE:
        cmd += ["-loop", "1", "-t", str(DURATION), "-i", str(scenes[name])]
    if bgm:
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]

    parts = [f"[0:v]{BG_FILTER}[bg]"]
    current = "bg"
    for index, (name, start, end) in enumerate(TIMELINE, start=1):
        fade_out_at = max(end - FADE, start)
        parts.append(
            f"[{index}:v]format=rgba,"
            f"fade=t=in:st={start}:d={FADE}:alpha=1,"
            f"fade=t=out:st={fade_out_at}:d={FADE}:alpha=1[s{index}]"
        )
        # 入场时向上滑 48px，比纯淡入更有"动起来"的感觉
        slide = f"if(lt(t,{start}+{FADE}),(1-(t-{start})/{FADE})*48,0)"
        parts.append(
            f"[{current}][s{index}]overlay=x=0:y='{slide}':eval=frame:"
            f"enable='between(t,{start},{end})'[v{index}]"
        )
        current = f"v{index}"
    filter_complex = ";".join(parts)

    cmd += ["-filter_complex", filter_complex, "-map", f"[{current}]"]
    if bgm:
        bgm_index = len(TIMELINE) + 1
        cmd += [
            "-map", f"{bgm_index}:a",
            "-af", f"afade=t=in:st=0:d=1,afade=t=out:st={DURATION - 2}:d=2,volume=0.8",
            "-c:a", "aac", "-b:a", "128k",
        ]
    else:
        cmd += ["-an"]
    cmd += [
        "-t", str(DURATION), "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    ]
    subprocess.run(cmd, check=True)
    return output


def compose_cover(bg_video: Path | None, layer: Path, output: Path, theme: str, at: float = 1.5) -> Path:
    """封面 = 压暗后的背景某一帧 + 封面图层。"""
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error"]
    if bg_video:
        cmd += ["-ss", str(at), "-i", str(bg_video)]
    else:
        cmd += _bg_input(None, theme)
    cmd += ["-i", str(layer), "-filter_complex", f"[0:v]{BG_FILTER}[bg];[bg][1:v]overlay=0:0",
            "-frames:v", "1", "-update", "1", str(output)]
    subprocess.run(cmd, check=True)
    return output


# ---------------------------------------------------------------------------
def generate(data: dict, out_dir: Path, video: Path | None = None, theme: str | None = None,
             bgm: Path | None = None, log=print) -> dict[str, Path]:
    theme = theme or data.get("theme") or "steel"
    out_dir.mkdir(parents=True, exist_ok=True)
    scenes_dir = out_dir / "scenes"
    scenes_dir.mkdir(exist_ok=True)

    scenes: dict[str, Path] = {}
    for name, _s, _e in TIMELINE:
        scenes[name] = render_scene_png(data, name, theme, scenes_dir / f"{name}.png")
        log(f"图层 {name} 完成")
    share_layer = render_scene_png(data, "cover", theme, scenes_dir / "cover_share.png", play=True)

    log(f"正在合成 {DURATION:.0f} 秒成片（约半分钟）")
    results = {
        "video": compose_video(video, scenes, out_dir / "video.mp4", theme, bgm),
        "cover": compose_cover(video, scenes["cover"], out_dir / "cover.png", theme),
        "cover_share": compose_cover(video, share_layer, out_dir / "cover_share.png", theme),
    }
    log(f"成片 {results['video']}  封面 {results['cover']}  分享图 {results['cover_share']}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="行情早报视频生成器")
    parser.add_argument("data", help="海报数据 JSON（与静态海报同一份）")
    parser.add_argument("--video", type=Path, help="用户上传的背景素材（mp4/mov），不填用动态渐变")
    parser.add_argument("--bgm", type=Path, help="背景音乐（mp3/m4a），可选")
    parser.add_argument("--theme", choices=list(THEME_ACCENT), help="强调色跟随的海报主题")
    parser.add_argument("--out", type=Path, default=Path("video_out"), help="输出目录")
    args = parser.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    generate(data, args.out, args.video, args.theme, args.bgm)


if __name__ == "__main__":
    main()
