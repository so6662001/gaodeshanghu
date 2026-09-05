from datetime import datetime, timedelta

import pytest

from app.models import Blacklist, SmsMessage, utcnow
from app.services.compliance import (
    add_to_blacklist,
    check_sendable,
    classify_reply,
    in_send_window,
    next_window_start,
    to_cn,
)


def test_blacklisted_phone_is_blocked(db):
    db.add(Blacklist(phone="13800138000", reason="unsubscribe"))
    db.commit()
    ok, reason = check_sendable(db, "13800138000")
    assert not ok and reason == "blacklist"


def test_landline_is_blocked(db):
    ok, reason = check_sendable(db, "057188888888")
    assert not ok and reason == "not_mobile"


def test_frequency_control_blocks_recent_touch(db):
    """7 天内发过就不能再发，这是防投诉的第一道闸。"""
    db.add(
        SmsMessage(phone="13800138000", content="x", status="sent", sent_at=utcnow())
    )
    db.commit()
    ok, reason = check_sendable(db, "13800138000", min_interval_days=7)
    assert not ok and reason == "too_frequent"


def test_frequency_control_allows_after_interval(db):
    db.add(
        SmsMessage(
            phone="13800138000", content="x", status="sent",
            sent_at=utcnow() - timedelta(days=10),
        )
    )
    db.commit()
    ok, _ = check_sendable(db, "13800138000", min_interval_days=7, max_per_month=5)
    assert ok


def test_monthly_cap(db):
    for days in (8, 12, 16):
        db.add(
            SmsMessage(
                phone="13800138000", content="x", status="sent",
                sent_at=utcnow() - timedelta(days=days),
            )
        )
    db.commit()
    ok, reason = check_sendable(db, "13800138000", min_interval_days=1, max_per_month=3)
    assert not ok and reason == "monthly_cap"


def test_add_to_blacklist_is_idempotent(db):
    add_to_blacklist(db, "13800138000")
    add_to_blacklist(db, "138 0013 8000")
    db.commit()
    assert db.query(Blacklist).count() == 1


@pytest.mark.parametrize(
    "content,expected",
    [
        ("TD", "unsubscribe"),
        ("td", "unsubscribe"),
        ("退订", "unsubscribe"),
        ("回T退订", "unsubscribe"),
        ("怎么合作", "interested"),
        ("价格多少", "interested"),
        ("再骚扰我就投诉", "complaint"),
        ("哦", "unknown"),
    ],
)
def test_classify_reply(content, expected):
    assert classify_reply(content) == expected


def test_send_window_boundaries(monkeypatch):
    """夜间不得打扰，凌晨的任务必须顺延到次日早上。"""
    import app.services.compliance as compliance

    midnight_utc = datetime(2026, 3, 5, 18, 0)  # 北京时间次日 02:00
    assert not in_send_window(midnight_utc)

    target = next_window_start(midnight_utc)
    assert to_cn(target).hour == 9

    noon_utc = datetime(2026, 3, 5, 4, 0)  # 北京时间 12:00
    assert in_send_window(noon_utc)
    assert next_window_start(noon_utc) == noon_utc
