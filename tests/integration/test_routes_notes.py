from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.routes_notes import router, safe_levenshtein_approximation

app = FastAPI()
app.include_router(router)
client = TestClient(app)

def test_safe_levenshtein_approximation():
    assert safe_levenshtein_approximation("abc", "abc") == 0
    assert safe_levenshtein_approximation("abc", "ab") == 1
    assert safe_levenshtein_approximation("", "abc") == 3
    assert safe_levenshtein_approximation("abc", "") == 3

def test_qa_metrics_endpoint():
    response = client.post("/api/notes/qa_metrics", json={
        "original_text": "Hello world",
        "parsed_text": "Hello world!"
    })
    assert response.status_code == 200
    data = response.json()
    assert "char_ratio" in data
    assert "token_ratio" in data
    assert "levenshtein_distance" in data
    assert data["levenshtein_distance"] == 1
