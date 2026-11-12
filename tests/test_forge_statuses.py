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
        ids = [status["id"] for status in history]
        assert all(isinstance(status_id, int) and status_id > 0 for status_id in ids)
        assert len(set(ids)) == 3
        assert ids == sorted(ids, reverse=True)
        assert combined["statuses"] == [history[0], history[2]]
        assert client.get(f"/repos/{REPO}/commits/{sha}/status").json() == combined
        assert client.get(f"{path}/statuses").json() == history
        assert (
            client.post("/_levers/ref_statuses", json={"repo": REPO, "ref": "main", "statuses": statuses}).status_code
            == 200
        )
        replacement = client.get(f"{path}/statuses").json()
        assert set(ids).isdisjoint(status["id"] for status in replacement)
        assert client.get(f"/repos/{REPO}/commits/ahead/statuses").json() == []
        assert client.post("/_levers/reset").status_code == 200
        assert client.get(f"{path}/statuses").json() == []
        assert (
            client.post("/_levers/ref_statuses", json={"repo": REPO, "ref": "main", "statuses": statuses}).status_code
            == 200
        )
        assert set(ids + [status["id"] for status in replacement]).isdisjoint(
            status["id"] for status in client.get(f"{path}/statuses").json()
        )


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
        assert client.get(f"/repos/{REPO}/commits/main/status").json()["total_count"] == 101
        assert (
            client.post(
                "/_levers/status_read_error", json={"repo": REPO, "ref": "main", "status_code": 200}
            ).status_code
            == 422
        )


def test_combined_and_checks_page_boundaries(tmp_path: Path) -> None:
    with forge(tmp_path) as client:
        base = f"/repos/{REPO}/commits/main"
        for count in (100, 101, 205):
            statuses = [{"context": f"ci-{i}", "state": "success"} for i in range(count)]
            statuses += [{"context": "ci-0", "state": "failure"}, {"context": "ci-0", "state": "error"}]
            assert (
                client.post(
                    "/_levers/ref_statuses", json={"repo": REPO, "ref": "main", "statuses": statuses}
                ).status_code
                == 200
            )
            checks = [{"name": f"check-{i}", "status": "success"} for i in range(count)]
            assert (
                client.post("/_levers/ref_checks", json={"repo": REPO, "ref": "main", "checks": checks}).status_code
                == 200
            )
            for endpoint, key in (("status", "statuses"), ("check-runs", "check_runs")):
                assert len(client.get(f"{base}/{endpoint}").json()[key]) == 30
                assert len(client.get(f"{base}/{endpoint}", params={"per_page": 1000}).json()[key]) == 100
                beyond = client.get(f"{base}/{endpoint}", params={"per_page": 100, "page": 99})
                assert beyond.json()["total_count"] == count
                assert beyond.json()[key] == []
                assert 'rel="prev"' in beyond.headers["link"]
                invalid = client.get(f"{base}/{endpoint}", params={"per_page": 0, "page": 0})
                assert invalid.json()["total_count"] == count
                assert len(invalid.json()[key]) == 1
                assert "page=2&per_page=1" in invalid.headers["link"]
                assert 'rel="next"' in invalid.headers["link"]
                collected: list[str] = []
                for page in range(1, (count + 99) // 100 + 1):
                    response = client.get(f"{base}/{endpoint}", params={"per_page": 100, "page": page})
                    body = response.json()
                    assert body["total_count"] == count
                    if endpoint == "status":
                        assert body["state"] == "success"
                    collected.extend(item["context" if endpoint == "status" else "name"] for item in body[key])
                    assert ('rel="next"' in response.headers.get("link", "")) == (page * 100 < count)
                assert len(collected) == count
                assert len(set(collected)) == count
                if endpoint == "status":
                    assert collected == [f"ci-{i}" for i in range(count)]


def test_empty_pages_and_aggregate_state_across_pages(tmp_path: Path) -> None:
    with forge(tmp_path) as client:
        base = f"/repos/{REPO}/commits/main"
        assert client.post("/_levers/ref_checks", json={"repo": REPO, "ref": "main", "checks": []}).status_code == 200
        for endpoint, key in (("status", "statuses"), ("check-runs", "check_runs")):
            response = client.get(f"{base}/{endpoint}")
            assert response.json()[key] == []
            assert response.json()["total_count"] == 0
            assert "link" not in response.headers
            if endpoint == "status":
                assert response.json()["state"] == "pending"

        statuses = [
            {"context": "first", "state": "success"},
            {"context": "second", "state": "failure"},
            {"context": "second", "state": "success"},
        ]
        assert (
            client.post("/_levers/ref_statuses", json={"repo": REPO, "ref": "main", "statuses": statuses}).status_code
            == 200
        )
        for page in (1, 2, 3):
            response = client.get(f"{base}/status", params={"page": page, "per_page": 1})
            assert response.json()["state"] == "failure"
            assert response.json()["total_count"] == 2
            links = response.links
            assert ("prev" in links) == (page > 1)
            assert ("next" in links) == (page < 2)
            assert len(response.json()["statuses"]) == (1 if page <= 2 else 0)
