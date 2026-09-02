import os
import tempfile

import pytest

# 测试必须用独立库并强制演练模式，避免误发真实短信
_tmp_db = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db}"
os.environ["SMS_DRY_RUN"] = "true"
os.environ["ADMIN_TOKEN"] = "test-token"
os.environ["AMAP_KEY"] = "test-key"

from app.db import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    Base.metadata.create_all(bind=engine)
    with TestClient(app) as test_client:
        test_client.headers.update({"X-Api-Token": "test-token"})
        yield test_client
    Base.metadata.drop_all(bind=engine)
