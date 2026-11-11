import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""

import pytest
from core.database import initialize_database, get_db_connection
from server.routes_state import get_persisted_app_settings, DEFAULT_BACKEND_SETTINGS
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.routes_state import router as state_router

@pytest.fixture(autouse=True)
def setup_db():
    initialize_database()

def test_default_backend_settings():
    settings = get_persisted_app_settings()
    for key, val in DEFAULT_BACKEND_SETTINGS.items():
        assert key in settings
        assert isinstance(settings[key], type(val))

def test_api_state_key_query():
    app = FastAPI()
    app.include_router(state_router)
    client = TestClient(app)

    # Save custom toggle
    save_payload = {
        "key": "app_persistence",
        "value": {
            "llmEnabled": False,
            "synthesisMode": "per_article_lm_summary",
            "defaultNoteMode": "notes",
            "sectionPopupPreview": False,
        }
    }
    res_post = client.post("/api/state", json=save_payload)
    assert res_post.status_code == 200

    # Test GET /api/state?key=app_persistence
    res_get = client.get("/api/state?key=app_persistence")
    assert res_get.status_code == 200
    data = res_get.json()
    assert data["llmEnabled"] is False
    assert data["synthesisMode"] == "per_article_lm_summary"
    assert data["defaultNoteMode"] == "notes"
    assert data["sectionPopupPreview"] is False

    # Check get_persisted_app_settings matches
    settings = get_persisted_app_settings()
    assert settings["llmEnabled"] is False
    assert settings["synthesisMode"] == "per_article_lm_summary"
    assert settings["defaultNoteMode"] == "notes"
    assert settings["sectionPopupPreview"] is False

    # Reset
    reset_payload = {
        "key": "app_persistence",
        "value": {
            "llmEnabled": True,
            "synthesisMode": "tantivy_microedit",
            "defaultNoteMode": "list",
            "sectionPopupPreview": True,
        }
    }
    client.post("/api/state", json=reset_payload)
    settings_reset = get_persisted_app_settings()
    assert settings_reset["llmEnabled"] is True
    assert settings_reset["synthesisMode"] == "tantivy_microedit"
