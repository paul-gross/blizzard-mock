"""``ForgeService`` — the forge's business rules over the git and state seams.

This is the domain layer: it resolves an id to its git/state objects at the
boundary and applies the rules (``bzh:domain-takes-objects``). It holds the
*write* seams (``bzh:controller-read-only``); all mutation flows through here.
"""

from __future__ import annotations

import fnmatch
import re
import zlib
from collections.abc import Mapping
from dataclasses import dataclass

from blizzard_mock.forge.domain.checks import CHECK_STATUSES, CheckSpec, ICheckRunStore
from blizzard_mock.forge.domain.clock import Clock
from blizzard_mock.forge.domain.errors import (
    BranchNotFound,
    CompareRefNotFound,
    HeadMismatch,
    IssueNotFound,
    LabelAlreadyExists,
    LabelNotFound,
    MergeConflict,
    MergeMethodNotAllowed,
    MergeRejected,
    NotFastForward,
    NotMergeable,
    ProtectedBranch,
    PullNotFound,
    RefAlreadyExists,
    RefMissing,
    RuleViolation,
    ValidationError,
)
from blizzard_mock.forge.domain.git import GitCommit, GitCompare, IWriteGitBackend
from blizzard_mock.forge.domain.levers import (
    ACTION_LEVERS,
    STATE_LEVERS,
    ILeverStore,
    Lever,
    LeverKind,
    LeverParams,
)
from blizzard_mock.forge.domain.models import (
    CheckRun,
    Comment,
    Issue,
    Label,
    MergeableState,
    MergeMethod,
    PullRequest,
    Repo,
    State,
)
from blizzard_mock.forge.domain.rate import IRateLedger
from blizzard_mock.forge.domain.state import IWriteForgeState

#: GitHub's account and repository name rules, which ``create_repository`` holds a new name to.
_OWNER_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
_REPO_NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")

#: The most pulls one listing page may hold, as on GitHub.
MAX_PER_PAGE = 100


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


@dataclass
class PullPage:
    """One page of a pull listing, with what a ``Link`` header is built from."""

    views: list[PullView]
    page: int
    per_page: int | None  # None: the request named no ``per_page``, so nothing is paged
    total: int

    @property
    def last_page(self) -> int:
        if self.per_page is None:
            return 1
        return max(-(-self.total // self.per_page), 1)


class ForgeService:
    """The composition-root-wired service every forge route delegates to."""

    def __init__(
        self,
        git: IWriteGitBackend,
        state: IWriteForgeState,
        levers: ILeverStore,
        clock: Clock,
        checks: ICheckRunStore,
        rate: IRateLedger,
    ) -> None:
        self._git = git
        self._state = state
        self._levers = levers
        self._clock = clock
        self._checks = checks
        self._rate = rate

    # -- repositories ------------------------------------------------------

    def get_repo(self, owner: str, name: str) -> Repo:
        """Resolve a backing repo (raises ``RepoNotFound``)."""
        return self._git.get_repo(owner, name)

    def create_repository(self, owner: str, name: str, files: Mapping[str, str]) -> Repo:
        """Create a bare repo seeded with ``files`` on ``main``. Issues, pulls, and
        labels start empty — the state store keys them by the new repo's full name."""
        if not _OWNER_NAME.fullmatch(owner):
            raise ValidationError(f"invalid owner: {owner}")
        if not _REPO_NAME.fullmatch(name) or name in (".", "..") or name.endswith(".git"):
            raise ValidationError(f"invalid repository name: {name}")
        return self._git.create(owner, name, files)

    def repository_origin(self, repo: Repo) -> str:
        return self._git.origin(repo)

    # -- issues ------------------------------------------------------------

    def list_issues(self, owner: str, name: str, state: str | None, labels: list[str] | None = None) -> list[Issue]:
        repo = self.get_repo(owner, name)
        return self._state.list_issues(repo.full_name, state, labels)

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

    # -- labels --------------------------------------------------------------

    def list_labels(self, owner: str, name: str) -> list[Label]:
        repo = self.get_repo(owner, name)
        return self._state.list_labels(repo.full_name)

    def create_label(self, owner: str, name: str, *, label_name: str) -> Label:
        repo = self.get_repo(owner, name)
        if self._state.get_label(repo.full_name, label_name) is not None:
            raise LabelAlreadyExists(f"label {label_name!r} already exists in {repo.full_name}")
        label = Label(name=label_name)
        self._state.put_label(repo.full_name, label)
        return label

    def add_issue_labels(self, owner: str, name: str, number: int, *, labels: list[str]) -> Issue:
        repo = self.get_repo(owner, name)
        issue = self.get_issue(owner, name, number)
        for label_name in labels:
            if label_name not in issue.labels:
                issue.labels.append(label_name)
        self._state.put_issue(repo.full_name, issue)
        return issue

    def remove_issue_label(self, owner: str, name: str, number: int, *, label_name: str) -> Issue:
        repo = self.get_repo(owner, name)
        issue = self.get_issue(owner, name, number)
        if label_name not in issue.labels:
            raise LabelNotFound(f"label {label_name!r} not set on issue #{number} in {repo.full_name}")
        issue.labels.remove(label_name)
        self._state.put_issue(repo.full_name, issue)
        return issue

    def set_issue_state(self, owner: str, name: str, number: int, *, state: str, state_reason: str | None) -> Issue:
        """Support ``PATCH .../issues/{n}`` closing an issue without a merge.
        Setting the same state again is a clean no-op, mirroring
        GitHub's own PATCH."""
        if state not in ("open", "closed"):
            raise ValidationError(f"invalid state: {state}")
        repo = self.get_repo(owner, name)
        issue = self.get_issue(owner, name, number)
        issue.state = State(state)
        issue.state_reason = state_reason
        issue.updated_at = self._clock.now()
        self._state.put_issue(repo.full_name, issue)
        return issue

    # -- pull requests -----------------------------------------------------

    def list_pulls(
        self,
        owner: str,
        name: str,
        state: str | None,
        *,
        base: str | None = None,
        head: str | None = None,
        sort: str | None = None,
        direction: str | None = None,
        page: int = 1,
        per_page: int | None = None,
    ) -> PullPage:
        """List pulls GitHub-style. With no ``per_page`` every match is page 1 and later
        pages are empty; a named ``per_page`` (capped at 100) pages the answer. Git views
        are resolved only for the page answered."""
        repo = self.get_repo(owner, name)
        if sort not in (None, "created", "updated"):
            raise ValidationError(f"invalid sort: {sort}")
        if direction not in (None, "asc", "desc"):
            raise ValidationError(f"invalid direction: {direction}")
        pulls = self._state.list_pulls(repo.full_name, state)
        if base is not None:
            pulls = [p for p in pulls if p.base == base]
        if head is not None:
            head_owner, sep, head_branch = head.rpartition(":")
            wrong_owner = bool(sep) and head_owner != repo.owner
            pulls = [] if wrong_owner else [p for p in pulls if p.head == head_branch]
        descending = direction == "desc" if direction else sort in (None, "created")
        pulls = sorted(
            pulls,
            key=lambda p: (p.updated_at if sort == "updated" else p.created_at, p.number),
            reverse=descending,
        )
        total = len(pulls)
        page = max(page, 1)
        if per_page is None:
            chosen = pulls if page == 1 else []
        else:
            per_page = min(max(per_page, 1), MAX_PER_PAGE)
            chosen = pulls[(page - 1) * per_page : page * per_page]
        return PullPage([self._view(repo, p) for p in chosen], page, per_page, total)

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
        for existing in self._state.list_pulls(repo.full_name, "open"):
            if existing.head == head and existing.base == base:
                raise ValidationError(f"A pull request already exists for {owner}:{head}.")
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
        if method is MergeMethod.MERGE and self._levers.find(LeverKind.MERGE_COMMITS_DISALLOWED, repo.full_name, None):
            raise MergeMethodNotAllowed("Merge commits are not allowed on this repository")
        if self._levers.find(LeverKind.MERGE_REJECTED, repo.full_name, number) is not None:
            lever = self._levers.find(LeverKind.MERGE_REJECTED, repo.full_name, number)
            reason = (lever.message if lever else None) or "merge was rejected by branch policy"
            raise MergeRejected(reason)
        if self._levers.find(LeverKind.MERGE_CONFLICT, repo.full_name, number) is not None:
            raise NotMergeable(f"pull request #{number} is not mergeable")
        head_sha = self._git.resolve_ref(repo, pull.head)
        if sha is not None and sha != head_sha:
            raise HeadMismatch("Head branch was modified. Review and try the merge again.")
        new_sha = self._land(repo, pull, method, message)
        self._record_merge(repo, pull, new_sha, merged_by=user, head_sha=head_sha)
        return MergeResult(new_sha, "Pull Request successfully merged")

    def _land(self, repo: Repo, pull: PullRequest, method: MergeMethod, message: str | None) -> str:
        """Land ``pull``'s head on its base by ``method``; every method really rewrites."""
        if method is MergeMethod.SQUASH:
            return self._git.squash(repo, pull.base, pull.head, message or f"{pull.title} (#{pull.number})")
        commit_message = message or f"Merge pull request #{pull.number} from {pull.head}"
        if method is MergeMethod.REBASE:
            return self._git.rebase(repo, pull.base, pull.head, commit_message)
        return self._git.merge(repo, pull.base, pull.head, commit_message)

    def merge_branches(self, owner: str, name: str, *, base: str, head: str, message: str | None) -> GitCommit | None:
        """Merge ``head`` into ``base`` outside any pull (``POST .../merges``). ``None`` is
        GitHub's 204: the base already holds the head."""
        repo = self.get_repo(owner, name)
        if not base or not head:
            raise ValidationError("both base and head are required")
        if not self._git.branch_exists(repo, base):
            raise CompareRefNotFound("Base does not exist")
        try:
            head_sha = self._git.resolve_ref(repo, head)
        except BranchNotFound as exc:
            raise CompareRefNotFound("Head does not exist") from exc
        self._refuse_by_ruleset(repo, base)
        if self._git.is_ancestor(repo, head_sha, base):
            return None
        try:
            sha = self._git.merge(repo, base, head_sha, message or f"Merge branch '{head}' into {base}")
        except NotMergeable as exc:
            raise MergeConflict("Merge conflict") from exc
        return self._git.get_commit(repo, sha)

    def is_merged(self, owner: str, name: str, number: int) -> bool:
        repo = self.get_repo(owner, name)
        return self._require_pull(repo, number).merged

    def update_branch(self, owner: str, name: str, number: int, *, expected_head_sha: str | None = None) -> str:
        """Merge base into the PR's head branch (``PUT .../update-branch``);
        self-heals a ``behind`` PR. A mismatched ``expected_head_sha`` is a 409
        (pinned by tests/test_forge_smoke.py::test_update_branch_stale_expected_head_is_409).
        """
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
        """Support ``PATCH .../pulls/{n}`` closing a PR without merge (D-065)."""
        repo = self.get_repo(owner, name)
        pull = self._require_pull(repo, number)
        if state is State.CLOSED and pull.state is not State.CLOSED:
            self._freeze(repo, pull)
        elif state is State.OPEN and not pull.merged:
            if not self._git.branch_exists(repo, pull.head):
                raise ValidationError(f"head ref does not exist: {pull.head}")
            pull.closed_head_sha = None
            pull.closed_base_sha = None
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
        _require_scope(kind, params)
        lever = Lever(
            kind=kind,
            repo=params.repo,
            number=params.number,
            remaining=params.remaining,
            message=params.message,
            branch=params.branch,
            pattern=params.pattern,
        )
        self._levers.arm(lever)
        return lever

    def clear_lever(
        self,
        kind: LeverKind,
        repo: str | None,
        number: int | None,
        branch: str | None = None,
        pattern: str | None = None,
    ) -> None:
        self._levers.clear(kind, repo, number, branch, pattern)

    def clear_all_levers(self) -> None:
        """Clear every lever, and with them the armed check runs and the rate windows."""
        self._levers.clear_all()
        self._checks.clear_all()
        self._rate.clear()

    def list_levers(self) -> list[Lever]:
        return self._levers.active()

    def _fire_action_lever(self, kind: LeverKind, params: LeverParams) -> None:
        if kind is LeverKind.EXTERNALLY_MERGED:
            self._external_merge(params)
        elif kind is LeverKind.COMMENT_MIDFLIGHT:
            self._comment_midflight(params)
        elif kind is LeverKind.REF_CHECKS:
            self._ref_checks(params)
        elif kind is LeverKind.RATE_BUDGET:
            self._rate.set_budget(params.token, remaining=params.remaining, reset=params.reset)

    def _ref_checks(self, params: LeverParams) -> None:
        """Arm named check runs against the revision ``ref`` resolves to now: a run of an
        existing name replaces it, a new name adds a run beside the others, and an empty
        list arms "no runs"."""
        if params.repo is None or params.ref is None or params.checks is None:
            raise ValidationError("ref_checks requires repo, ref, and checks")
        for spec in params.checks:
            if spec.status not in CHECK_STATUSES:
                raise ValidationError(f"invalid check status: {spec.status}")
        owner, name = _split_full_name(params.repo)
        repo = self.get_repo(owner, name)
        sha = self._git.resolve_ref(repo, params.ref)
        if not params.checks:
            self._checks.replace_all(repo.full_name, sha, [])
        for spec in params.checks:
            self._checks.put(repo.full_name, sha, self._named_check_run(sha, spec))

    def _named_check_run(self, sha: str, spec: CheckSpec) -> CheckRun:
        status, conclusion = CHECK_STATUSES[spec.status]
        return CheckRun(
            id=zlib.crc32(f"{sha}/{spec.name}".encode()),
            name=spec.name,
            status=status,
            conclusion=conclusion,
            head_sha=sha,
        )

    def _external_merge(self, params: LeverParams) -> None:
        """Land the PR's head on its base directly, bypassing the PR flow (D-065)."""
        if params.repo is None or params.number is None:
            raise ValidationError("externally_merged requires repo and number")
        owner, name = _split_full_name(params.repo)
        repo = self.get_repo(owner, name)
        pull = self._require_pull(repo, params.number)
        if pull.merged:
            return
        head_sha = self._git.resolve_ref(repo, pull.head)
        method = params.method or MergeMethod.MERGE
        message = f"Merge pull request #{pull.number} from {pull.head} (external)"
        if method is MergeMethod.SQUASH:
            message = f"{pull.title} (#{pull.number}) (external)"
        new_sha = self._land(repo, pull, method, message)
        self._record_merge(repo, pull, new_sha, merged_by="external", head_sha=head_sha)

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

    def _record_merge(self, repo: Repo, pull: PullRequest, sha: str, *, merged_by: str, head_sha: str) -> None:
        now = self._clock.now()
        pull.merged = True
        pull.merged_at = now
        pull.merged_by = merged_by
        pull.merge_commit_sha = sha
        pull.closed_head_sha = head_sha
        pull.closed_base_sha = sha
        pull.state = State.CLOSED
        pull.updated_at = now
        self._state.put_pull(repo.full_name, pull)

    def _view(self, repo: Repo, pull: PullRequest) -> PullView:
        # A closed PR's head sha is frozen when it closes, not tracked live — real GitHub
        # never updates a merged or closed PR's own `head.sha` after a later, unrelated
        # push, and the head branch may be gone altogether.
        head_sha = (
            pull.closed_head_sha
            if pull.state is State.CLOSED and pull.closed_head_sha is not None
            else self._git.resolve_ref(repo, pull.head)
        )
        try:
            base_sha = self._git.resolve_ref(repo, pull.base)
        except BranchNotFound:
            if pull.closed_base_sha is None:
                raise
            base_sha = pull.closed_base_sha
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
        if self._levers.find(LeverKind.CHECKS_FAILED, repo.full_name, pull.number) is not None:
            # A red check run — an armed PR must be a coherent world, never a
            # green PR with a red check.
            return True, MergeableState.BLOCKED
        if self._git.is_mergeable(repo, pull.base, pull.head):
            return True, MergeableState.CLEAN
        return False, MergeableState.DIRTY

    def list_check_runs(self, owner: str, name: str, ref: str) -> list[CheckRun]:
        """Check runs against ``ref``. Runs armed against its revision by ``ref_checks``
        are answered as they are; otherwise they are derived live from the active lever
        set. ``checks_failed``/``base_checks_failed`` win over ``checks_pending``; absent a
        lever, the ref reads green. Correlated by resolved commit sha, not branch name
        (pinned by tests/test_pin_mock.py).
        """
        repo = self.get_repo(owner, name)
        sha = self._git.resolve_ref(repo, ref)
        armed = self._checks.runs(repo.full_name, sha)
        if armed is not None:
            return armed
        pull = self._find_pull_by_head_sha(repo, sha)
        pr_number = pull.number if pull is not None else None
        if pull is not None and self._levers.find(LeverKind.CHECKS_FAILED, repo.full_name, pr_number) is not None:
            return [self._synthetic_check_run(sha, status="completed", conclusion="failure")]
        base_lever = self._levers.find(LeverKind.BASE_CHECKS_FAILED, repo.full_name, None)
        if base_lever is not None and sha == self._git.resolve_ref(repo, repo.default_branch):
            return [self._synthetic_check_run(sha, status="completed", conclusion="failure")]
        if pull is not None and self._levers.find(LeverKind.CHECKS_PENDING, repo.full_name, pr_number) is not None:
            return [self._synthetic_check_run(sha, status="in_progress", conclusion=None)]
        return [self._synthetic_check_run(sha, status="completed", conclusion="success")]

    def _find_pull_by_head_sha(self, repo: Repo, sha: str) -> PullRequest | None:
        """The open PR whose head branch currently resolves to ``sha``, if any."""
        for pull in self._state.list_pulls(repo.full_name, "open"):
            if self._git.resolve_ref(repo, pull.head) == sha:
                return pull
        return None

    def _synthetic_check_run(self, sha: str, *, status: str, conclusion: str | None) -> CheckRun:
        return CheckRun(
            id=abs(hash((sha, status, conclusion))) % (10**8),
            name="ci",
            status=status,
            conclusion=conclusion,
            head_sha=sha,
        )

    def commit(self, owner: str, name: str, ref: str) -> GitCommit:
        repo = self.get_repo(owner, name)
        return self._git.get_commit(repo, ref)

    def compare(self, owner: str, name: str, base: str, head: str) -> GitCompare:
        repo = self.get_repo(owner, name)
        try:
            return self._git.compare(repo, base, head)
        except BranchNotFound as exc:
            raise CompareRefNotFound(str(exc)) from exc

    def resolve_ref(self, owner: str, name: str, ref: str) -> str:
        repo = self.get_repo(owner, name)
        return self._git.resolve_ref(repo, ref)

    def update_ref(self, owner: str, name: str, ref: str, *, sha: str, force: bool) -> str:
        """Update a ref's target sha (GitHub's ``PATCH .../git/refs/{ref}``).

        A protected branch refuses any update. Unless ``force``, the update is also
        rejected (``NotFastForward``) unless the current sha is an ancestor of ``sha``.
        """
        repo = self.get_repo(owner, name)
        if any(lever.branch == ref for lever in self._matching(LeverKind.BRANCH_PROTECTED, repo)):
            raise ProtectedBranch(f"Protected branch update failed for refs/heads/{ref}.")
        current_sha = self._git.resolve_ref(repo, ref)
        target_sha = self._git.resolve_ref(repo, sha)
        if not force and not self._git.is_ancestor(repo, current_sha, target_sha):
            raise NotFastForward(f"Update is not a fast forward: refs/heads/{ref}")
        self._git.update_ref(repo, ref, target_sha)
        return target_sha

    def create_ref(self, owner: str, name: str, ref: str, *, sha: str) -> str:
        """Create a branch (GitHub's ``POST .../git/refs``); ``ref`` is ``refs/heads/<branch>``."""
        repo = self.get_repo(owner, name)
        branch = ref.removeprefix("refs/heads/")
        if branch == ref or not branch:
            raise ValidationError("Reference name is invalid")
        self._refuse_by_ruleset(repo, branch)
        if self._git.branch_exists(repo, branch):
            raise RefAlreadyExists("Reference already exists")
        try:
            target_sha = self._git.resolve_ref(repo, sha)
        except BranchNotFound as exc:
            raise ValidationError("Object does not exist") from exc
        self._git.create_ref(repo, branch, target_sha)
        return target_sha

    def delete_ref(self, owner: str, name: str, branch: str) -> None:
        """Delete a branch (GitHub's ``DELETE .../git/refs/heads/{b}``). A pull the
        branch heads or is based on closes with it, as on GitHub."""
        repo = self.get_repo(owner, name)
        if not self._git.branch_exists(repo, branch):
            raise RefMissing("Reference does not exist")
        for pull in self._state.list_pulls(repo.full_name, "open"):
            if branch in (pull.head, pull.base):
                self._freeze(repo, pull)
                pull.state = State.CLOSED
                pull.updated_at = self._clock.now()
                self._state.put_pull(repo.full_name, pull)
        self._git.delete_ref(repo, branch)

    def _freeze(self, repo: Repo, pull: PullRequest) -> None:
        """Pin the shas a pull closes with, so it still lists once its branches move or go."""
        pull.closed_head_sha = self._git.resolve_ref(repo, pull.head)
        pull.closed_base_sha = self._git.resolve_ref(repo, pull.base)

    def _matching(self, kind: LeverKind, repo: Repo) -> list[Lever]:
        return [lever for lever in self._levers.active() if lever.kind is kind and lever.matches(repo.full_name, None)]

    def _refuse_by_ruleset(self, repo: Repo, branch: str) -> None:
        for lever in self._matching(LeverKind.RULESET, repo):
            if lever.pattern is not None and fnmatch.fnmatchcase(branch, lever.pattern):
                raise RuleViolation(
                    f"Repository rule violations found\n\nBranch {branch} is covered by the rule {lever.pattern}."
                )


def _require_scope(kind: LeverKind, params: LeverParams) -> None:
    """The levers whose scope is a branch or a pattern name it, and the repo it is in."""
    if kind is LeverKind.BRANCH_PROTECTED and (params.repo is None or params.branch is None):
        raise ValidationError("branch_protected requires repo and branch")
    if kind is LeverKind.RULESET and (params.repo is None or params.pattern is None):
        raise ValidationError("ruleset requires repo and pattern")
    if kind is LeverKind.MERGE_COMMITS_DISALLOWED and params.repo is None:
        raise ValidationError("merge_commits_disallowed requires repo")


def _split_full_name(full_name: str) -> tuple[str, str]:
    owner, _, name = full_name.partition("/")
    if not owner or not name:
        raise ValidationError(f"malformed repo full name: {full_name!r}")
    return owner, name
