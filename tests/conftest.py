import os
import sys
import tempfile
from pathlib import Path

import pytest

# main.py reads the config at import time: point it to a throwaway database and upload dir
_tmp = tempfile.mkdtemp(prefix="null_limite_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def db():
    main.Base.metadata.drop_all(bind=main.engine)
    main.Base.metadata.create_all(bind=main.engine)
    session = main.SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db):
    return TestClient(main.app)


def make_user(db, email, role=main.UserRole.PROFESSIONAL, **fields):
    user = main.Professional(email=email, full_name=email.split("@")[0], role=role,
                             hashed_password=main.get_password_hash("demo123"), **fields)
    db.add(user)
    db.commit()
    return user


def auth(client, email):
    token = client.post("/auth/login", data={"username": email, "password": "demo123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def manager(db, client):
    make_user(db, "gestor@nexora.demo", role=main.UserRole.MANAGER)
    return auth(client, "gestor@nexora.demo")
