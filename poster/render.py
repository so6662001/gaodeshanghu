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
import tempfile
import time
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
WIDTH = 750

THEMES = {
    "steel": "深邃钢蓝：深色高对比、钢材斜纹质感，默认风格，通用",
    "paper": "极简白：研报感，适合正式客户、企业采购群",
    "terminal": "财经终端：黑底荧光、等宽数字，给盯盘型老板",
    "industrial": "工业橙黑：大色块、直角、硬朗，给工程总包、重型行业",
    "kraft": "暖纸墨韵：牛皮纸底、朱砂红、宋体标题，商圈熟客群有人情味",
    "sky": "清新蓝白：轻盈圆润、互联网感，给年轻采购与平台新用户",
}


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


def _resource(item: dict, market: dict[str, int]) -> str:
    """一条优势现货。核心是把「为什么找我买」显性化：比均价低多少、有多少、能不能提。"""
    price = int(item["price"])
    ref = item.get("vs_market")
    if ref is None and item.get("name") in market:
        ref = price - market[item["name"]]
    vs_html = ""
    if ref is not None:
        ref = int(ref)
        if ref < 0:
            vs_html = f'<div class="vs below">低于均价 {abs(ref)}</div>'
        elif ref > 0:
            vs_html = f'<div class="vs above">高于均价 {ref}</div>'
        else:
            vs_html = '<div class="vs above">与均价持平</div>'

    meta = []
    if item.get("origin"):
        meta.append(f"<span>钢厂 <b>{escape(str(item['origin']))}</b></span>")
    if item.get("warehouse"):
        meta.append(f"<span>{escape(str(item['warehouse']))}</span>")
    if item.get("qty"):
        meta.append(f"<span>现货 <b>{escape(str(item['qty']))}</b></span>")

    tags = "".join(
        f'<span class="tag{" hot" if i == 0 and item.get("hot") else ""}">{escape(str(t))}</span>'
        for i, t in enumerate(item.get("tags", []))
    )
    hot = " hot" if item.get("hot") else ""
    return (
        f'<div class="row{hot}">'
        f'<div><div class="n"><span class="name">{escape(str(item["name"]))}</span>'
        f'<span class="spec">{escape(str(item.get("spec", "")))}</span></div>'
        f'<div class="meta">{"".join(meta)}</div>'
        f'{f"<div class=tags>{tags}</div>" if tags else ""}</div>'
        f'<div class="p"><div class="price num">{price:,}<small>元/吨</small></div>{vs_html}</div>'
        f"</div>"
    )


def render_html(data: dict, theme: str | None = None) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    theme = theme or data.get("theme") or "steel"
    if theme not in THEMES:
        raise SystemExit(f"未知风格 {theme}，可选：{', '.join(THEMES)}")
    cards = "\n".join(_card(p) for p in data.get("prices", []))
    market = {p["name"]: int(p["price"]) for p in data.get("prices", [])}
    resources = "\n".join(_resource(r, market) for r in data.get("resources", []))

    avatar = _img_tag(data.get("avatar_image", "")) or escape(data.get("avatar_text", "普"))
    qr = _img_tag(data.get("qr_image", "")) or (
        '<div style="width:100%;height:100%;background:'
        "repeating-conic-gradient(#111 0 25%, #fff 0 50%) 0 0/16px 16px;"
        'border-radius:6px"></div>'
    )

    # headline / summary 允许带 <b> <span class="hl"> 等少量标记，其余字段转义
    raw_fields = {"headline", "summary", "insight"}
    values = {
        "theme": theme,
        "cards": cards,
        "resources": resources,
        "avatar": avatar,
        "qr": qr,
        "res_note": escape(str(data.get("res_note", ""))),
        "res_more": escape(str(data.get("res_more", ""))),
    }
    for key, value in data.items():
        if key in ("prices", "resources", "avatar_image", "avatar_text", "qr_image", "theme"):
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


def render_png(data: dict, output: Path, height: int = 1930, theme: str | None = None) -> Path:
    html = render_html(data, theme)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
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
        # 部分容器环境没有 dbus，Chrome 截图完成后不会自行退出，
        # 所以不等进程结束，文件落地且大小稳定就直接收工
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 40
        last_size = -1
        try:
            while time.time() < deadline:
                if proc.poll() is not None and output.exists():
                    break
                if output.exists():
                    size = output.stat().st_size
                    if size and size == last_size:
                        break
                    last_size = size
                time.sleep(0.3)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        if not output.exists():
            raise SystemExit("海报渲染失败，请确认 Chrome 可正常启动")
    return output


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="每日行情海报生成器")
    parser.add_argument("data", help="海报数据 JSON")
    parser.add_argument("output", nargs="?", default="poster.png", help="输出 PNG 路径或 --all 时的目录")
    parser.add_argument("--theme", choices=list(THEMES), help="风格，不填读 JSON 里的 theme，默认 steel")
    parser.add_argument("--all", action="store_true", help="一次输出全部风格到目录，用于挑选")
    parser.add_argument("--list", action="store_true", help="列出可用风格")
    args = parser.parse_args()

    if args.list:
        for key, desc in THEMES.items():
            print(f"{key:<12}{desc}")
        return

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    if args.all:
        outdir = Path(args.output if args.output != "poster.png" else "posters")
        outdir.mkdir(parents=True, exist_ok=True)
        for key in THEMES:
            render_png(data, outdir / f"{key}.png", theme=key)
            print(f"已生成 {outdir / f'{key}.png'}  ({THEMES[key]})")
        return

    output = Path(args.output)
    render_png(data, output, theme=args.theme)
    print(f"已生成 {output} ({WIDTH*2}px 宽，2x 高清)")


if __name__ == "__main__":
    main()
