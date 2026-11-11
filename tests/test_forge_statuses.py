"""Commit status histories and scoped read failures."""

from pathlib import Path

from tests.forge_world import REPO, forge, sha_of


def test_status_history_and_combined_status(tmp_path: Path) -> None:
    with forge(tmp_path) as client:
        path = f"/repos/{REPO}/commits/main"
        sha = sha_of(client, "main")
        assert client.get(f"{path}/statuses").json() == []
        assert client.get(f"{path}/status").json()["state"] == "pending"
        statuses = [
            {"context": "ci", "state": "success"},
            {"context": "ci", "state": "failure"},
            {"context": "lint", "state": "pending"},
        ]
        assert (
            client.post("/_levers/ref_statuses", json={"repo": REPO, "ref": "main", "statuses": statuses}).status_code
            == 200
        )
        history = client.get(f"/repos/{REPO}/commits/{sha}/statuses").json()
        assert [s["state"] for s in history] == ["success", "failure", "pending"]
        combined = client.get(f"{path}/status").json()
        assert combined["state"] == "pending"
        assert combined["total_count"] == 2
        assert client.get(f"/repos/{REPO}/commits/ahead/statuses").json() == []
        assert client.post("/_levers/reset").status_code == 200
        assert client.get(f"{path}/statuses").json() == []


def test_status_scoped_read_errors_and_pagination(tmp_path: Path) -> None:
    with forge(tmp_path) as client:
        path = f"/repos/{REPO}/commits/main/statuses"
        statuses = [{"context": f"ci-{i}", "state": "success"} for i in range(101)]
        assert (
            client.post("/_levers/ref_statuses", json={"repo": REPO, "ref": "main", "statuses": statuses}).status_code
            == 200
        )
        first = client.get(path, params={"per_page": 100})
        assert len(first.json()) == 100
        assert 'rel="next"' in first.headers["link"]
        assert len(client.get(path, params={"per_page": 100, "page": 2}).json()) == 1
        lever = {"repo": REPO, "ref": "main", "status_code": 401}
        assert client.post("/_levers/status_read_error", json=lever).status_code == 200
        assert client.get(path).status_code == 401
        assert client.get(f"/repos/{REPO}/commits/main/status").status_code == 401
        assert client.get(f"/repos/{REPO}/commits/ahead/statuses").status_code == 200
        assert client.get(f"/repos/{REPO}/commits/main/check-runs").status_code == 200
        lever = {"repo": REPO, "ref": "main", "status_code": None}
        assert client.post("/_levers/status_read_error", json=lever).status_code == 200
        assert client.get(path).status_code == 200
        assert (
            client.post(
                "/_levers/status_read_error", json={"repo": REPO, "ref": "main", "status_code": 200}
            ).status_code
            == 422
        )
