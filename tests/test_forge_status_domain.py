from blizzard_mock.forge.domain.statuses import Status, combined_status


def test_combined_status_uses_latest_per_context_and_failure_precedence() -> None:
    def status(context: str, state: str, id: int) -> Status:
        return Status.model_validate({"context": context, "state": state, "id": id})

    history = [
        status("ci", "success", 4),
        status("lint", "pending", 3),
        status("ci", "error", 2),
        status("ci", "failure", 1),
    ]
    assert combined_status([]) == ("pending", [])
    assert combined_status(history) == ("pending", history[:2])
    assert combined_status([history[0]]) == ("success", history[:1])
    assert combined_status([history[1], history[2]]) == ("failure", [history[1], history[2]])
