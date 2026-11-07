"""Artifact check verdicts and request validation over the runner-facing boundary."""

from fastapi.testclient import TestClient

from blizzard_mock.mock_hub.app import create_app


def test_artifact_verdicts_follow_seeded_graph_formats_without_mutating_chunk() -> None:
    app = create_app()
    client = TestClient(app)
    token = client.post("/api/runners", json={"name": "runner"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    chunk = client.post(
        "/_seed/chunk",
        json={
            "entry": "b",
            "nodes": {"b": {}},
            "status": "done",
            "artifact_formats": {
                "delta": ["finding-delta"],
                "docket": ["garden-proposal-candidate"],
                "both": ["garden-proposal-candidate", "finding-delta"],
            },
        },
    ).json()["chunk_id"]
    path = f"/api/fleet/chunks/{chunk}/artifact-check"
    before = app.state.service._state.get_chunk(chunk).model_dump()

    def check(name: str, content: str) -> object:
        response = client.post(path, headers=headers, json={"name": name, "content": content})
        assert response.status_code == 200
        return response.json()["refusal"]

    assert check("delta", '{"scope":"s","measurement":1}') == (
        "artifact 'delta' does not match the finding-delta shape: measurement: Input should be a valid string"
    )
    assert check("delta", '{"scope":"s","findings":[{"op":"add","class":" ","locus":"l","summary":"s"}]}') == (
        "artifact 'delta' does not match the finding-delta shape: findings.0.add.class: Value error, must not be blank"
    )
    assert check("delta", '{"scope":"s"}') is None
    assert check("docket", '[{"ref":"r","class":"c","title":"t","body":"b"}]') is None
    assert check("docket", "{}") == (
        "artifact 'docket' does not match the garden-proposal-candidate shape: <root>: Input should be a valid array"
    )
    assert str(check("both", "{}")).startswith("artifact 'both' does not match the finding-delta shape:")
    assert check("unvalidated", "not json") is None
    assert client.post(path, headers=headers, json={"name": "delta"}).status_code == 422
    assert client.post(path, json={"name": "delta", "content": "x"}).status_code == 401
    assert app.state.service._state.get_chunk(chunk).model_dump() == before
