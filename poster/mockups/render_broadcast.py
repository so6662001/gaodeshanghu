"""「行情直播间」视频高保真效果图渲染。

    python poster/mockups/render_broadcast.py --bg-dir 素材目录 --out poster/samples/broadcast

素材目录内放 7 张实拍帧 f1.jpg … f7.jpg（用户自己拍的仓库 / 装车 / 工地画面），
脚本把每一幕的信息图层叠上去，输出 1080×1920 效果图与拼接分镜。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import render as poster  # noqa: E402

TEMPLATE = HERE / "broadcast_frame.html"

SPARK = """
<svg viewBox="0 0 1000 200" preserveAspectRatio="none">
  <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">
    <stop offset="0" stop-color="#ff4d4f" stop-opacity=".55"/><stop offset="1" stop-color="#ff4d4f" stop-opacity="0"/></linearGradient></defs>
  <path d="M0 150 L80 158 L160 140 L240 146 L320 120 L400 128 L480 104 L560 110 L640 86 L720 92 L800 60 L880 52 L1000 20 L1000 200 L0 200 Z" fill="url(#g)"/>
  <path d="M0 150 L80 158 L160 140 L240 146 L320 120 L400 128 L480 104 L560 110 L640 86 L720 92 L800 60 L880 52 L1000 20" fill="none" stroke="#ff4d4f" stroke-width="6" stroke-linejoin="round"/>
  <circle cx="1000" cy="20" r="14" fill="#ff4d4f" stroke="#fff" stroke-width="5"/>
</svg>"""

FRAMES = [
    {
        "id": "f1", "title": "片头 0–4s", "cls": "f1", "clock": "08:00",
        "subtitle": "早上好，今天是 9 月 5 日，这里是<em>普讯钢贸行情早报</em>。",
        "content": """
<div class="hero">
  <div class="kicker">今日行情早报</div>
  <h1>南京钢材<br/>行情早报<span>4 涨 1 平 1 跌 · 均价重心上移</span></h1>
  <div class="meta">
    <div><small>播报</small>黄好庭 · 普讯钢贸顾问</div>
    <div><small>数据截至</small>08:00 · 6 大品种</div>
  </div>
</div>""",
    },
    {
        "id": "f2", "title": "全景行情 4–11s", "cls": "", "clock": "08:00",
        "subtitle": "南京市场 6 个主流品种，<em>4 涨 1 平 1 跌</em>，均价重心继续上移。",
        "content": """
<div class="board">
  <div class="h"><div class="b"></div><div class="t">今日行情</div><div class="s">元/吨 · 较昨日</div></div>
  <div class="grid">
    <div class="cell lead"><div class="n">热卷</div><div class="p num">3,620</div><div class="c up">▲ +40</div></div>
    <div class="cell"><div class="n">热镀方管</div><div class="p num">4,220</div><div class="c up">▲ +30</div></div>
    <div class="cell"><div class="n">无缝管</div><div class="p num">4,180</div><div class="c up">▲ +20</div></div>
    <div class="cell"><div class="n">方管</div><div class="p num">3,560</div><div class="c up">▲ +10</div></div>
    <div class="cell"><div class="n">H型钢</div><div class="p num">3,340</div><div class="c flat">— 持平</div></div>
    <div class="cell"><div class="n">圆钢</div><div class="p num">3,340</div><div class="c down">▼ -20</div></div>
  </div>
  <div class="summary">节前补库启动，<b>刚需可按需采购</b>；投机单谨慎追高。</div>
</div>""",
    },
    {
        "id": "f3", "title": "领涨特写 11–16s", "cls": "", "clock": "08:01",
        "subtitle": "<em>热卷领涨</em>，上调 40 元报 3620，创两周新高。",
        "content": f"""
<div class="focus">
  <div class="tagline">今日领涨 · 两周新高</div>
  <div class="name">热卷</div>
  <div class="price"><div class="v num">3,620</div><div class="u">元/吨</div></div>
  <div class="chg"><div class="big up num">▲ +40</div><div class="pill">本周累计 <span class="up">+90</span></div><div class="pill">沙钢 · 马钢 主流报价</div></div>
  <div class="spark"><div class="lab"><span>近 14 日走势</span><span>08.22 → 09.05</span></div>{SPARK}</div>
</div>""",
    },
    {
        "id": "f4", "title": "今日看点 16–23s", "cls": "", "clock": "08:01",
        "subtitle": "华东库存连续三周回落，下游节前赶工，<em>需求有支撑</em>。",
        "content": """
<div class="quote">
  <div class="k">今日看点</div>
  <div class="q">华东库存<em>连续三周回落</em>，下游工程节前赶工，需求有支撑。</div>
  <div class="tips">
    <div><b>刚需</b>按需备货，节前物流紧张提前订车</div>
    <div><b>投机</b>谨慎追高，关注下周一钢厂调价</div>
  </div>
</div>
<div class="lower3"><div class="av">黄</div><div class="who"><div class="n">黄好庭</div><div class="r">普讯钢贸顾问 · 12 年钢贸从业</div></div></div>""",
    },
    {
        "id": "f5", "title": "优势现货 23–31s", "cls": "", "clock": "08:02",
        "subtitle": "六合仓热卷现货 320 吨，报 3580，<em>比市场均价低 40</em>，今天就能提。",
        "content": """
<div class="stock">
  <div class="h"><div class="b"></div><div class="t">今日优势现货</div><div class="s">价格截至 08:00 · 电话确认为准</div></div>
  <div class="row hot">
    <div><div class="nm"><b>热卷</b><span>Q235B 3.0×1500×C</span></div>
      <div class="mt"><span>钢厂 <b>沙钢</b></span><span>南京六合仓</span><span>现货 <b>320 吨</b></span></div>
      <div class="tg"><span class="hot">一手货源</span><span>今日可提</span><span>可开 13% 票</span></div></div>
    <div class="pr"><div class="v num">3,580<small>元/吨</small></div><div class="vs">低于均价 40</div></div>
  </div>
  <div class="row ghost">
    <div><div class="nm"><b>热镀方管</b><span>40×40×2.0 6m</span></div>
      <div class="mt"><span>钢厂 <b>友发</b></span><span>南京浦口仓</span><span>现货 <b>180 吨</b></span></div></div>
    <div class="pr"><div class="v num">4,180<small>元/吨</small></div><div class="vs">低于均价 40</div></div>
  </div>
</div>""",
    },
    {
        "id": "f6", "title": "装车配送 31–37s", "cls": "", "clock": "08:02",
        "subtitle": "浦口仓热镀方管 180 吨，<em>整车优惠，市区免费配送</em>，下午就能到工地。",
        "content": """
<div class="callout"><div class="k">浦口仓 · 今日发运</div><div class="v">热镀方管 180 吨<small>友发 · 40×40×2.0 · 4,180 元/吨</small></div></div>
<div class="stock">
  <div class="row hot">
    <div><div class="nm"><b>整车优惠</b><span>30 吨起</span></div>
      <div class="mt"><span>市区 <b>免费配送</b></span><span>当天下单 · 当天到场</span></div>
      <div class="tg"><span class="hot">整车优惠</span><span>免费配送市区</span><span>可定尺切割</span></div></div>
    <div class="pr"><div class="v num">4,180<small>元/吨</small></div><div class="vs">低于均价 40</div></div>
  </div>
</div>""",
    },
    {
        "id": "f7", "title": "片尾 37–42s", "cls": "blur", "clock": "08:03",
        "subtitle": "更多现货和实时报价，<em>扫码进店</em>。我们明早 8 点再见。",
        "content": """
<div class="end">
  <div class="k">货袋子 · 认证商家店铺</div>
  <h2>扫码进店<em>·</em>实时报价</h2>
  <div class="qrbox"><div class="qr"></div><div class="t">普讯钢贸 货袋子店铺</div><div class="s">长按识别 · 12 项现货资源 · 在线询价下单</div></div>
  <div class="who"><div class="av">黄</div><div><div class="n">黄好庭</div><div class="r">普讯钢贸顾问 · 每天 8:00 准时播报</div></div></div>
  <div class="tel num">139 **** 2982</div>
  <div class="badges"><span>一手货源</span><span>今日可提</span><span>可开 13% 票</span></div>
</div>""",
    },
]


def screenshot(html: str, output: Path, size: tuple[int, int]) -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        page = Path(tmp) / "frame.html"
        page.write_text(html, encoding="utf-8")
        cmd = [
            poster.find_chrome(), "--headless=new", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", f"--user-data-dir={tmp}/profile", "--hide-scrollbars",
            "--force-device-scale-factor=1", f"--window-size={size[0]},{size[1]}",
            f"--screenshot={output.resolve()}", page.as_uri(),
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


def render(frame: dict, bg: Path, output: Path) -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    for key, value in {
        "bg": bg.resolve().as_uri(), "frame_class": frame["cls"], "content": frame["content"],
        "subtitle": frame["subtitle"], "clock": frame["clock"],
    }.items():
        html = html.replace(f"{{{{{key}}}}}", value)
    screenshot(html, output, (1080, 1920))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bg-dir", type=Path, required=True, help="f1.jpg…f7.jpg 实拍帧目录")
    parser.add_argument("--out", type=Path, default=HERE.parent / "samples" / "broadcast")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    outputs = []
    for frame in FRAMES:
        bg = args.bg_dir / f"{frame['id']}.jpg"
        png = args.out / f"{frame['id']}.png"
        render(frame, bg, png)
        out = png.with_suffix(".jpg")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(png), "-q:v", "2", str(out)], check=True)
        png.unlink()
        outputs.append(out)
        print(f"{frame['id']} {frame['title']} → {out}")

    inputs = [arg for path in outputs for arg in ("-i", str(path))]
    stack = "".join(f"[{i}]" for i in range(len(outputs))) + f"hstack={len(outputs)}"
    sheet = args.out / "storyboard.jpg"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
                    "-filter_complex", f"{stack},scale=3150:-1", "-q:v", "3", str(sheet)], check=True)
    print(f"分镜拼图 → {sheet}")

    board = args.out / "board.png"
    screenshot((HERE / "broadcast_board.html").read_text(encoding="utf-8"), board, (3200, 1180))
    print(f"分镜音轨设计板 → {board}")


if __name__ == "__main__":
    main()
