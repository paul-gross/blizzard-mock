"""``ForgeService`` — the forge's business rules over the git and state seams.

This is the domain layer: it resolves an id to its git/state objects at the
boundary and applies the rules (``bzh:domain-takes-objects`` — the rule-bearing
helpers ``_mergeability`` / merge take resolved ``Repo`` / ``PullRequest``
objects). It holds the *write* seams (``bzh:controller-read-only`` — routers
hold only this service, never a store); all mutation flows through here. Every
collaborator is injected at the composition root (``bzh:dependency-injection``).
"""

from __future__ import annotations

from blizzard_mock.forge.domain.clock import Clock
from blizzard_mock.forge.domain.errors import (
    HeadMismatch,
    IssueNotFound,
    MergeRejected,
    NotFastForward,
    NotMergeable,
    PullNotFound,
    ValidationError,
)
from blizzard_mock.forge.domain.git import GitCommit, IWriteGitBackend
from blizzard_mock.forge.domain.levers import (
    ACTION_LEVERS,
    STATE_LEVERS,
    ILeverStore,
    Lever,
    LeverKind,
    LeverParams,
)
from blizzard_mock.forge.domain.models import (
    Comment,
    Issue,
    MergeableState,
    MergeMethod,
    PullRequest,
    Repo,
    State,
)
from blizzard_mock.forge.domain.state import IWriteForgeState


class MergeResult:
    """Outcome of a successful merge — the GitHub ``PUT .../merge`` 200 body."""

    def __init__(self, sha: str, message: str) -> None:
        self.sha = sha
        self.merged = True
        self.message = message


class PullView:
    """A pull request plus its live, git-computed mergeability — what the
    ``GET .../pulls/{n}`` route serializes."""

    def __init__(
        self,
        pull: PullRequest,
        head_sha: str,
        base_sha: str,
        mergeable: bool | None,
        mergeable_state: MergeableState,
    ) -> None:
        self.pull = pull
        self.head_sha = head_sha
        self.base_sha = base_sha
        self.mergeable = mergeable
        self.mergeable_state = mergeable_state


class ForgeService:
    """The composition-root-wired service every forge route delegates to."""

    def __init__(
        self,
        git: IWriteGitBackend,
        state: IWriteForgeState,
        levers: ILeverStore,
        clock: Clock,
    ) -> None:
        self._git = git
        self._state = state
        self._levers = levers
        self._clock = clock

    # -- repositories ------------------------------------------------------

    def get_repo(self, owner: str, name: str) -> Repo:
        """Resolve a backing repo (raises ``RepoNotFound``)."""
        return self._git.get_repo(owner, name)

    # -- issues ------------------------------------------------------------

    def list_issues(self, owner: str, name: str, state: str | None) -> list[Issue]:
        repo = self.get_repo(owner, name)
        return self._state.list_issues(repo.full_name, state)

    def get_issue(self, owner: str, name: str, number: int) -> Issue:
        repo = self.get_repo(owner, name)
        issue = self._state.get_issue(repo.full_name, number)
        if issue is None:
            raise IssueNotFound(f"issue #{number} not found in {repo.full_name}")
        return issue

    def create_issue(self, owner: str, name: str, *, title: str, body: str, user: str, labels: list[str]) -> Issue:
        repo = self.get_repo(owner, name)
        if not title.strip():
            raise ValidationError("issue title can't be blank")
        now = self._clock.now()
        issue = Issue(
            number=self._state.next_number(repo.full_name),
            title=title,
            body=body,
            user=user,
            labels=labels,
            created_at=now,
            updated_at=now,
        )
        self._state.put_issue(repo.full_name, issue)
        return issue

    def list_issue_comments(self, owner: str, name: str, number: int) -> list[Comment]:
        return self.get_issue(owner, name, number).comments

    def create_issue_comment(self, owner: str, name: str, number: int, *, body: str, user: str) -> Comment:
        repo = self.get_repo(owner, name)
        # Numbers are shared; a comment may target an issue or a pull request.
        if self._state.get_issue(repo.full_name, number) is None and (
            self._state.get_pull(repo.full_name, number) is None
        ):
            raise IssueNotFound(f"issue #{number} not found in {repo.full_name}")
        comment = self._new_comment(repo.full_name, body=body, user=user)
        if self._state.get_issue(repo.full_name, number) is not None:
            self._state.add_issue_comment(repo.full_name, number, comment)
        else:
            self._state.add_pull_comment(repo.full_name, number, comment)
        return comment

    def _new_comment(self, repo_full: str, *, body: str, user: str) -> Comment:
        now = self._clock.now()
        return Comment(
            id=self._state.next_comment_id(repo_full),
            body=body,
            user=user,
            created_at=now,
            updated_at=now,
        )

    # -- pull requests -----------------------------------------------------

    def list_pulls(self, owner: str, name: str, state: str | None) -> list[PullView]:
        repo = self.get_repo(owner, name)
        return [self._view(repo, p) for p in self._state.list_pulls(repo.full_name, state)]

    def get_pull(self, owner: str, name: str, number: int) -> PullView:
        repo = self.get_repo(owner, name)
        pull = self._require_pull(repo, number)
        return self._view(repo, pull)

    def create_pull(self, owner: str, name: str, *, title: str, body: str, head: str, base: str, user: str) -> PullView:
        repo = self.get_repo(owner, name)
        if not head or not base:
            raise ValidationError("both head and base are required")
        if not self._git.branch_exists(repo, head):
            raise ValidationError(f"head ref does not exist: {head}")
        if not self._git.branch_exists(repo, base):
            raise ValidationError(f"base ref does not exist: {base}")
        now = self._clock.now()
        pull = PullRequest(
            number=self._state.next_number(repo.full_name),
            title=title,
            body=body,
            head=head,
            base=base,
            user=user,
            created_at=now,
            updated_at=now,
        )
        self._state.put_pull(repo.full_name, pull)
        return self._view(repo, pull)

    def merge_pull(
        self,
        owner: str,
        name: str,
        number: int,
        *,
        method: MergeMethod,
        message: str | None,
        sha: str | None,
        user: str,
    ) -> MergeResult:
        repo = self.get_repo(owner, name)
        pull = self._require_pull(repo, number)
        if pull.merged or pull.state is State.CLOSED:
            raise NotMergeable(f"pull request #{number} is not mergeable")
        if self._levers.find(LeverKind.MERGE_REJECTED, repo.full_name, number) is not None:
            lever = self._levers.find(LeverKind.MERGE_REJECTED, repo.full_name, number)
            reason = (lever.message if lever else None) or "merge was rejected by branch policy"
            raise MergeRejected(reason)
        if self._levers.find(LeverKind.MERGE_CONFLICT, repo.full_name, number) is not None:
            raise NotMergeable(f"pull request #{number} is not mergeable")
        head_sha = self._git.resolve_ref(repo, pull.head)
        if sha is not None and sha != head_sha:
            raise HeadMismatch("Head branch was modified. Review and try the merge again.")
        commit_message = message or f"Merge pull request #{number} from {pull.head}"
        new_sha = self._git.merge(repo, pull.base, pull.head, commit_message)
        self._record_merge(repo, pull, new_sha, merged_by=user)
        return MergeResult(new_sha, "Pull Request successfully merged")

    def is_merged(self, owner: str, name: str, number: int) -> bool:
        repo = self.get_repo(owner, name)
        return self._require_pull(repo, number).merged

    def update_branch(self, owner: str, name: str, number: int, *, expected_head_sha: str | None = None) -> str:
        """Merge base into the PR's head branch (GitHub's ``PUT .../update-branch``).

        The self-heal a ``behind`` PR needs: advances the head with the latest base
        (a real merge commit, so ``head.sha`` moves — mirroring GitHub) and clears any
        ``stale_branch`` lever so the PR next reads ``clean``. Guarded on
        ``expected_head_sha`` like the real API — a mismatch is a 409 (``HeadMismatch``),
        so a caller that stacked reads never double-updates a moved head. ``behind``
        implies mergeable, so the update itself never conflicts in the mock."""
        repo = self.get_repo(owner, name)
        pull = self._require_pull(repo, number)
        if pull.merged or pull.state is State.CLOSED:
            raise NotMergeable(f"pull request #{number} is not mergeable")
        head_sha = self._git.resolve_ref(repo, pull.head)
        if expected_head_sha is not None and expected_head_sha != head_sha:
            raise HeadMismatch("Head branch was modified. Review and try the merge again.")
        self._git.merge(repo, pull.head, pull.base, f"Merge branch '{pull.base}' into {pull.head}")
        lever = self._levers.find(LeverKind.STALE_BRANCH, repo.full_name, number)
        if lever is not None:
            self._levers.clear(lever.kind, lever.repo, lever.number)
        return "Updating pull request branch."

    def set_pull_state(self, owner: str, name: str, number: int, *, state: State) -> PullView:
        """Support ``PATCH .../pulls/{n}`` closing a PR without merge — the
        close-without-merge terminal disposition a delivery flow treats as
        complete (D-065)."""
        repo = self.get_repo(owner, name)
        pull = self._require_pull(repo, number)
        pull.state = state
        pull.updated_at = self._clock.now()
        self._state.put_pull(repo.full_name, pull)
        return self._view(repo, pull)

    # -- levers ------------------------------------------------------------

    def arm_lever(self, kind: LeverKind, params: LeverParams) -> Lever | None:
        """Arm a state lever, or fire an action lever immediately. Returns the
        armed ``Lever`` for state levers; ``None`` for action levers."""
        if kind in ACTION_LEVERS:
            self._fire_action_lever(kind, params)
            return None
        if kind not in STATE_LEVERS:  # pragma: no cover - exhaustive guard
            raise ValidationError(f"unknown lever: {kind}")
        lever = Lever(
            kind=kind,
            repo=params.repo,
            number=params.number,
            remaining=params.remaining,
            message=params.message,
        )
        self._levers.arm(lever)
        return lever

    def clear_lever(self, kind: LeverKind, repo: str | None, number: int | None) -> None:
        self._levers.clear(kind, repo, number)

    def clear_all_levers(self) -> None:
        self._levers.clear_all()

    def list_levers(self) -> list[Lever]:
        return self._levers.active()

    def _fire_action_lever(self, kind: LeverKind, params: LeverParams) -> None:
        if kind is LeverKind.EXTERNALLY_MERGED:
            self._external_merge(params)
        elif kind is LeverKind.COMMENT_MIDFLIGHT:
            self._comment_midflight(params)

    def _external_merge(self, params: LeverParams) -> None:
        """Land the PR's head on its base directly — the external merge a
        polling delivery flow must detect (D-065)."""
        if params.repo is None or params.number is None:
            raise ValidationError("externally_merged requires repo and number")
        owner, name = _split_full_name(params.repo)
        repo = self.get_repo(owner, name)
        pull = self._require_pull(repo, params.number)
        if pull.merged:
            return
        message = f"Merge pull request #{pull.number} from {pull.head} (external)"
        new_sha = self._git.merge(repo, pull.base, pull.head, message)
        self._record_merge(repo, pull, new_sha, merged_by="external")

    def _comment_midflight(self, params: LeverParams) -> None:
        if params.repo is None or params.number is None or params.body is None:
            raise ValidationError("comment_midflight requires repo, number, and body")
        owner, name = _split_full_name(params.repo)
        self.create_issue_comment(owner, name, params.number, body=params.body, user=params.user)

    # -- internals ---------------------------------------------------------

    def _require_pull(self, repo: Repo, number: int) -> PullRequest:
        pull = self._state.get_pull(repo.full_name, number)
        if pull is None:
            raise PullNotFound(f"pull request #{number} not found in {repo.full_name}")
        return pull

    def _record_merge(self, repo: Repo, pull: PullRequest, sha: str, *, merged_by: str) -> None:
        now = self._clock.now()
        pull.merged = True
        pull.merged_at = now
        pull.merged_by = merged_by
        pull.merge_commit_sha = sha
        pull.state = State.CLOSED
        pull.updated_at = now
        self._state.put_pull(repo.full_name, pull)

    def _view(self, repo: Repo, pull: PullRequest) -> PullView:
        head_sha = self._git.resolve_ref(repo, pull.head)
        base_sha = self._git.resolve_ref(repo, pull.base)
        mergeable, mstate = self._mergeability(repo, pull)
        return PullView(pull, head_sha, base_sha, mergeable, mstate)

    def _mergeability(self, repo: Repo, pull: PullRequest) -> tuple[bool | None, MergeableState]:
        """Live mergeability: the ``merge_conflict`` lever forces dirty; a merged
        or closed PR is unknown; otherwise it is computed against real refs."""
        if pull.merged or pull.state is State.CLOSED:
            return None, MergeableState.UNKNOWN
        if self._levers.find(LeverKind.MERGE_CONFLICT, repo.full_name, pull.number) is not None:
            return False, MergeableState.DIRTY
        if self._levers.find(LeverKind.STALE_BRANCH, repo.full_name, pull.number) is not None:
            # Behind, not conflicted: mergeable once the branch is updated.
            return True, MergeableState.BEHIND
        if self._levers.find(LeverKind.CHECKS_PENDING, repo.full_name, pull.number) is not None:
            # Content-mergeable but blocked by required checks/reviews.
            return True, MergeableState.BLOCKED
        if self._git.is_mergeable(repo, pull.base, pull.head):
            return True, MergeableState.CLEAN
        return False, MergeableState.DIRTY

    def commit(self, owner: str, name: str, ref: str) -> GitCommit:
        repo = self.get_repo(owner, name)
        return self._git.get_commit(repo, ref)

    def resolve_ref(self, owner: str, name: str, ref: str) -> str:
        repo = self.get_repo(owner, name)
        return self._git.resolve_ref(repo, ref)

    def update_ref(self, owner: str, name: str, ref: str, *, sha: str, force: bool) -> str:
        """Update a ref's target sha (GitHub's ``PATCH .../git/refs/{ref}``).

        Unless ``force`` is set, this is an atomic compare-and-swap: the
        update is rejected (``NotFastForward``) unless the ref's current sha
        is an ancestor of ``sha`` — the fast-forward, PR-free delivery path."""
        repo = self.get_repo(owner, name)
        current_sha = self._git.resolve_ref(repo, ref)
        target_sha = self._git.resolve_ref(repo, sha)
        if not force and not self._git.is_ancestor(repo, current_sha, target_sha):
            raise NotFastForward(f"Update is not a fast forward: refs/heads/{ref}")
        self._git.update_ref(repo, ref, target_sha)
        return target_sha


def _split_full_name(full_name: str) -> tuple[str, str]:
    owner, _, name = full_name.partition("/")
    if not owner or not name:
        raise ValidationError(f"malformed repo full name: {full_name!r}")
    return owner, name
