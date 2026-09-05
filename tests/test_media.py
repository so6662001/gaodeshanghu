"""宣传物料：视频图层构建与任务接口（不依赖 Chrome / ffmpeg）。"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "poster"))

import video as poster_video  # noqa: E402

from app.api import routes_media  # noqa: E402

DATA = json.loads((ROOT / "poster" / "data.example.json").read_text(encoding="utf-8"))


def test_timeline_is_contiguous():
    for (_, _, end), (_, start, _) in zip(poster_video.TIMELINE, poster_video.TIMELINE[1:]):
        assert end == start
    assert poster_video.TIMELINE[0][1] == 0
    assert poster_video.DURATION == poster_video.TIMELINE[-1][2]


def test_build_scene_html_activates_only_target_scene():
    html = poster_video.build_scene_html(DATA, "market", "industrial")
    active = re.findall(r'<section[^>]*data-active[^>]*>', html)
    assert len(active) == 1 and 'data-scene="market"' in active[0]
    assert "{{" not in html, "不应残留占位符"
    assert "--accent: #ff6a00" in html
    assert "热卷" in html and "整体偏强" in html


def test_cover_share_mode_adds_play_button_and_channel_name():
    html = poster_video.build_scene_html(DATA, "cover", "steel", play=True)
    cover = re.search(r'<section class="scene cover"[^>]*>', html).group(0)
    assert "data-active" in cover and "data-play" in cover
    assert "普讯钢贸行情" in html


def test_kpis_pick_biggest_movers():
    prices = [
        {"name": "A", "price": 100, "change": 5},
        {"name": "B", "price": 100, "change": -30},
        {"name": "C", "price": 100, "change": 0},
        {"name": "D", "price": 100, "change": 12},
    ]
    html = poster_video._kpis(prices, limit=2)
    assert "▼ -30" in html and "▲ +12" in html and ">A<" not in html


def test_trend_word():
    assert poster_video._trend_word([{"change": 1}, {"change": 2}]) == "全线上涨"
    assert poster_video._trend_word([{"change": -1}, {"change": -2}]) == "全线回落"
    assert poster_video._trend_word([{"change": 1}, {"change": -2}]) == "涨跌互现"


@pytest.fixture
def no_render(monkeypatch):
    submitted = []
    monkeypatch.setattr(routes_media._executor, "submit", lambda *args: submitted.append(args))
    return submitted


def test_create_job_validates_payload(client, no_render):
    r = client.post("/api/media/jobs", data={"kind": "video", "theme": "steel", "data": "not json"})
    assert r.status_code == 400
    r = client.post("/api/media/jobs", data={"kind": "video", "theme": "steel", "data": "{}"})
    assert r.status_code == 400
    r = client.post("/api/media/jobs", data={"kind": "gif", "theme": "steel", "data": json.dumps(DATA)})
    assert r.status_code == 422


def test_create_job_rejects_wrong_file_type(client, no_render):
    r = client.post(
        "/api/media/jobs",
        data={"kind": "video", "theme": "steel", "data": json.dumps(DATA)},
        files={"video": ("evil.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert not no_render


def test_create_job_queues_and_lists(client, no_render):
    r = client.post(
        "/api/media/jobs",
        data={"kind": "video", "theme": "kraft", "data": json.dumps(DATA)},
        files={"video": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")},
    )
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "queued" and job["has_video"] is True and job["theme"] == "kraft"
    assert len(no_render) == 1
    assert client.get("/api/media/jobs").json()[0]["id"] == job["id"]
    assert client.get(f"/api/media/jobs/{job['id']}").json()["kind"] == "video"


def test_download_requires_token_and_blocks_traversal(client, no_render):
    job_id = "20260905000000-abcdef"
    assert client.get(f"/api/media/jobs/{job_id}/video.mp4", headers={"X-Api-Token": ""}).status_code == 401
    assert client.get(f"/api/media/jobs/{job_id}/video.mp4?token=test-token").status_code == 404
    assert client.get(f"/api/media/jobs/../../.env?token=test-token").status_code in (404, 422)
    assert client.get(f"/api/media/jobs/{job_id}/..%2F..%2F.env?token=test-token").status_code == 404


def test_example_and_themes(client):
    assert client.get("/api/media/example").json()["prices"]
    keys = [t["key"] for t in client.get("/api/media/themes").json()]
    assert "steel" in keys and len(keys) == 6
