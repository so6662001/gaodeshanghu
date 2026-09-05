"""每日行情海报生成器。

用法：
    python poster/render.py poster/data.example.json out.png

只依赖本机 Chrome/Chromium，无需安装任何 Python 包。
把 data.json 里的价格和涨跌换成当天真实数据，每天早上跑一次即可。
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
WIDTH = 750


def _img_tag(path_or_url: str) -> str:
    if not path_or_url:
        return ""
    p = Path(path_or_url)
    if p.exists():
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        data = base64.b64encode(p.read_bytes()).decode()
        return f'<img src="data:{mime};base64,{data}" />'
    return f'<img src="{escape(path_or_url)}" />'


def _card(item: dict) -> str:
    change = int(item.get("change", 0))
    week = item.get("week")
    if change > 0:
        cls, arrow, text = "up", "▲", f"+{change}"
    elif change < 0:
        cls, arrow, text = "down", "▼", f"{change}"
    else:
        cls, arrow, text = "flat", "—", "持平"
    week_html = ""
    if week is not None:
        sign = "+" if week > 0 else ""
        week_html = f'<div class="wk">周环比 {sign}{week}</div>'
    lead = " lead" if item.get("lead") else ""
    return (
        f'<div class="card{lead}">'
        f'<div class="k">{escape(str(item["name"]))}</div>'
        f'<div class="v num">{int(item["price"]):,}<small>元/吨</small></div>'
        f'<div class="chg {cls}"><span class="arrow">{arrow}</span>{text}</div>'
        f"{week_html}</div>"
    )


def render_html(data: dict) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    cards = "\n".join(_card(p) for p in data.get("prices", []))

    avatar = _img_tag(data.get("avatar_image", "")) or escape(data.get("avatar_text", "普"))
    qr = _img_tag(data.get("qr_image", "")) or (
        '<div style="width:100%;height:100%;background:'
        "repeating-conic-gradient(#111 0 25%, #fff 0 50%) 0 0/16px 16px;"
        'border-radius:6px"></div>'
    )

    # headline / summary 允许带 <b> <span class="hl"> 等少量标记，其余字段转义
    raw_fields = {"headline", "summary", "insight"}
    values = {
        "cards": cards,
        "avatar": avatar,
        "qr": qr,
    }
    for key, value in data.items():
        if key in ("prices", "avatar_image", "avatar_text", "qr_image"):
            continue
        values[key] = str(value) if key in raw_fields else escape(str(value))

    for key, value in values.items():
        html = html.replace(f"{{{{{key}}}}}", value)
    return html


def find_chrome() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("未找到 Chrome / Chromium，请先安装浏览器")


def render_png(data: dict, output: Path, height: int = 1400) -> Path:
    html = render_html(data)
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "poster.html"
        html_path.write_text(html, encoding="utf-8")
        cmd = [
            find_chrome(),
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            f"--user-data-dir={tmp}/profile",
            "--hide-scrollbars",
            "--force-device-scale-factor=2",  # 2x 清晰度，微信压缩后仍然锐利
            f"--window-size={WIDTH},{height}",
            f"--screenshot={output.resolve()}",
            html_path.as_uri(),
        ]
        output.unlink(missing_ok=True)
        try:
            # 部分容器环境没有 dbus，Chrome 截图完成后不会自行退出，以文件产出为准
            subprocess.run(cmd, capture_output=True, timeout=40)
        except subprocess.TimeoutExpired:
            pass
        if not output.exists():
            raise SystemExit("海报渲染失败，请确认 Chrome 可正常启动")
    return output


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = Path(sys.argv[2] if len(sys.argv) > 2 else "poster.png")
    render_png(data, output)
    print(f"已生成 {output} ({WIDTH*2}px 宽，2x 高清)")


if __name__ == "__main__":
    main()
