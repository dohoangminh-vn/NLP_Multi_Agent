"""API FastAPI + SSE (chế độ offline)."""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from src.api.main import app  # noqa: E402

client = TestClient(app)
BODY = {"target_bai": 20, "topic_name": "Câu lệnh lặp for", "scope_mode": "45m_practice",
        "basic_ratio": 0.25, "standard_ratio": 0.5, "advanced_ratio": 0.25}


def events(resp):
    out = []
    for block in resp.text.split("\n\n"):
        if block.strip():
            ev = dict(line.split(": ", 1) for line in block.splitlines())
            out.append((ev["event"], json.loads(ev["data"])))
    return out


def assert_pairs(evs):
    open_ = set()
    for e, d in evs:
        if e == "node_start":
            assert d["node"] not in open_, f"node_start lặp: {d['node']}"
            open_.add(d["node"])
        elif e == "node_end":
            assert d["node"] in open_, f"node_end thiếu start: {d['node']}"
            open_.discard(d["node"])
    if any(e == "final" for e, _ in evs):
        assert not open_, f"node_start không có node_end: {open_}"


def test_meta_and_health():
    assert client.get("/api/health").json()["status"] == "ok"
    meta = client.get("/api/meta").json()
    assert [l["bai"] for l in meta["lessons"]] == list(range(16, 30))


def test_full_flow_with_hitl():
    ev = events(client.post("/api/lesson/start", json=BODY))
    names = [e for e, _ in ev]
    assert names[0] == "session" and names[-1] == "hitl_required" and "final" not in names
    assert_pairs(ev)
    tid = ev[0][1]["thread_id"]
    outline = ev[-1][1]["outline"]
    assert sum(outline["activity_minutes"]) == 45
    assert client.get(f"/api/lesson/{tid}").json()["status"] == "waiting_hitl"
    assert client.post("/api/lesson/resume", json={"thread_id": tid, "outline": {**outline, "activity_minutes": [5, 5, 5, 5]}}).status_code == 422
    ev2 = events(client.post("/api/lesson/resume", json={"thread_id": tid, "outline": outline}))
    assert_pairs(ev2)
    assert [e for e, _ in ev2][-2:] == ["final", "done"]
    final = ev2[-2][1]
    assert all(v["ast_pass"] and v["sandbox_pass"] for v in final["verification"])
    assert client.get(f"/api/lesson/{tid}").json()["status"] == "done"
    assert client.post("/api/lesson/resume", json={"thread_id": tid, "outline": outline}).status_code == 409


def test_bypass_goes_straight_to_final():
    ev = events(client.post("/api/lesson/start", json={**BODY, "bypass_hitl": True, "scope_mode": "90m_full"}))
    assert_pairs(ev)
    names = [e for e, _ in ev]
    assert "hitl_required" not in names and names[-1] == "done"
    assert ev[-2][1]["plan"]["activities"] and sum(a["duration_minutes"] for a in ev[-2][1]["plan"]["activities"]) == 90


def test_validation_errors():
    assert client.post("/api/lesson/start", json={**BODY, "advanced_ratio": 0.6}).status_code == 422
    assert client.post("/api/lesson/start", json={**BODY, "target_bai": 31}).status_code == 422
    assert client.get("/api/lesson/khongco").status_code == 404


def test_demo_stream_red_then_green():
    ev = events(client.get("/api/demo/stream?speed=1000"))
    assert [d["status"] for e, d in ev if e == "critic"] == ["red", "green"]
    assert not any(e.startswith("_") for e, _ in ev)


def test_export_docx_real_and_mock():
    pytest.importorskip("docx")
    import io

    import docx

    ev = events(client.post("/api/lesson/start", json={**BODY, "bypass_hitl": True}))
    tid = ev[0][1]["thread_id"]
    r = client.get(f"/api/lesson/{tid}/export")
    assert r.status_code == 200 and "Bai20" in r.headers["content-disposition"]
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(r.content)).paragraphs)
    assert "KẾ HOẠCH BÀI DẠY" in text and "PHIẾU HỌC TẬP" in text
    assert client.get("/api/lesson/mock-bai20/export").status_code == 200
    assert client.get("/api/lesson/khongco/export").status_code == 404
    assert client.get("/api/demo/stream?scenario=khongco").status_code == 404
