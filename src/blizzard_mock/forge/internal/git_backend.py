"""GitPython adapter for the bare-repo backing model (``IWriteGitBackend``).

All git usage is confined to this file: resolves ``owner/name`` to a bare
repo, reads refs, computes mergeability with ``git merge-tree``, and performs
a real merge via a throwaway linked worktree.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from git import Repo
from git.exc import GitCommandError, InvalidGitRepositoryError, NoSuchPathError

from blizzard_mock.forge.domain.errors import BranchNotFound, RepoNotFound
from blizzard_mock.forge.domain.git import GitAuthor, GitCommit
from blizzard_mock.forge.domain.models import Repo as RepoModel
from blizzard_mock.forge.internal.errors import GitErrorFactory

#: Committer identity stamped on forge-performed merge commits.
_MERGE_IDENTITY = {
    "GIT_AUTHOR_NAME": "blizzard-mock forge",
    "GIT_AUTHOR_EMAIL": "forge@blizzard-mock.local",
    "GIT_COMMITTER_NAME": "blizzard-mock forge",
    "GIT_COMMITTER_EMAIL": "forge@blizzard-mock.local",
}


class GitBackend:
    """Bare-repo git backend. Implements ``IWriteGitBackend``."""

    def __init__(self, repos_dir: Path, error_factory: GitErrorFactory) -> None:
        self._root = repos_dir
        self._errors = error_factory
        self._cache: dict[str, Repo] = {}

    # -- resolution --------------------------------------------------------

    def _candidates(self, owner: str, name: str) -> list[Path]:
        return [
            self._root / owner / f"{name}.git",
            self._root / owner / name,
            self._root / f"{name}.git",
            self._root / name,
            self._root / f"{owner}__{name}.git",
            self._root / f"{owner}__{name}",
        ]

    def _open(self, owner: str, name: str) -> Repo:
        full = f"{owner}/{name}"
        cached = self._cache.get(full)
        if cached is not None:
            return cached
        for path in self._candidates(owner, name):
            if not path.exists():
                continue
            try:
                repo = Repo(path)
            except (InvalidGitRepositoryError, NoSuchPathError):
                continue
            self._cache[full] = repo
            return repo
        raise RepoNotFound(f"no bare repo backs {full} under {self._root}")

    def get_repo(self, owner: str, name: str) -> RepoModel:
        repo = self._open(owner, name)
        return RepoModel(owner=owner, name=name, default_branch=self._default_branch(repo))

    def _default_branch(self, repo: Repo) -> str:
        try:
            ref = repo.git.symbolic_ref("HEAD")
        except GitCommandError:
            return "main"
        return ref.removeprefix("refs/heads/")

    # -- reads -------------------------------------------------------------

    def branch_exists(self, repo: RepoModel, branch: str) -> bool:
        git = self._open(repo.owner, repo.name)
        try:
            git.git.rev_parse("--verify", "--quiet", f"refs/heads/{branch}")
        except GitCommandError:
            return False
        return True

    def resolve_ref(self, repo: RepoModel, ref: str) -> str:
        git = self._open(repo.owner, repo.name)
        for candidate in (f"refs/heads/{ref}", ref):
            try:
                return git.git.rev_parse("--verify", f"{candidate}^{{commit}}")
            except GitCommandError:
                continue
        raise BranchNotFound(f"ref does not resolve in {repo.full_name}: {ref}")

    def get_commit(self, repo: RepoModel, ref: str) -> GitCommit:
        git = self._open(repo.owner, repo.name)
        sha = self.resolve_ref(repo, ref)
        commit = git.commit(sha)
        author = GitAuthor(
            name=str(commit.author.name),
            email=str(commit.author.email),
            date=commit.authored_datetime.isoformat(),
        )
        return GitCommit(
            sha=commit.hexsha,
            message=str(commit.message),
            author=author,
            parents=[p.hexsha for p in commit.parents],
        )

    def is_mergeable(self, repo: RepoModel, base: str, head: str) -> bool:
        git = self._open(repo.owner, repo.name)
        base_sha = self.resolve_ref(repo, base)
        head_sha = self.resolve_ref(repo, head)
        try:
            git.git.merge_tree("--write-tree", base_sha, head_sha)
        except GitCommandError:
            return False
        return True

    def is_ancestor(self, repo: RepoModel, ancestor: str, descendant: str) -> bool:
        git = self._open(repo.owner, repo.name)
        anc = self.resolve_ref(repo, ancestor)
        desc = self.resolve_ref(repo, descendant)
        try:
            git.git.merge_base("--is-ancestor", anc, desc)
        except GitCommandError:
            return False
        return True

    # -- writes ------------------------------------------------------------

    def merge(self, repo: RepoModel, base: str, head: str, message: str) -> str:
        head_sha = self.resolve_ref(repo, head)
        # Attached to `base`, so the merge commit advances that branch ref directly —
        # no explicit update_ref needed, unlike rebase's detached worktree below.
        with (
            self._throwaway_worktree(repo, checkout_ref=base, detach=False) as work,
            work.git.custom_environment(**_MERGE_IDENTITY),
        ):
            try:
                work.git.merge("--no-ff", "-m", message, head_sha)
            except GitCommandError as exc:
                self._raise_op_error(
                    exc, work, repo, base, head, op="merge", conflict_markers=("CONFLICT", "Automatic merge failed")
                )
            return work.git.rev_parse("HEAD")

    def rebase(self, repo: RepoModel, base: str, head: str, message: str) -> str:
        git = self._open(repo.owner, repo.name)
        head_sha = self.resolve_ref(repo, head)
        # Detached, not on the head branch, so replaying its commits never rewrites the
        # head ref itself — only base is advanced explicitly below.
        with (
            self._throwaway_worktree(repo, checkout_ref=head_sha, detach=True) as work,
            work.git.custom_environment(**_MERGE_IDENTITY),
        ):
            try:
                work.git.rebase(base)
            except GitCommandError as exc:
                self._raise_op_error(
                    exc, work, repo, base, head, op="rebase", conflict_markers=("CONFLICT", "could not apply")
                )
            new_sha = work.git.rev_parse("HEAD")
        git.git.update_ref(f"refs/heads/{base}", new_sha)
        return new_sha

    def update_ref(self, repo: RepoModel, ref: str, sha: str) -> None:
        git = self._open(repo.owner, repo.name)
        git.git.update_ref(f"refs/heads/{ref}", sha)

    @contextlib.contextmanager
    def _throwaway_worktree(self, repo: RepoModel, *, checkout_ref: str, detach: bool) -> Iterator[Repo]:
        """A throwaway linked worktree at ``checkout_ref``, torn down on the way out
        whether the block raises or not — the seam :meth:`merge` and :meth:`rebase` share
        for their only real difference: attached to a branch (merge) or detached at a sha
        (rebase, so replay never touches the head branch's own ref)."""
        git = self._open(repo.owner, repo.name)
        tmp = Path(tempfile.mkdtemp(prefix="forge-worktree-"))
        try:
            flag = "--detach" if detach else "--checkout"
            git.git.worktree("add", "--force", flag, str(tmp), checkout_ref)
            yield Repo(tmp)
        finally:
            with contextlib.suppress(GitCommandError):
                git.git.worktree("remove", "--force", str(tmp))
            shutil.rmtree(tmp, ignore_errors=True)

    def _raise_op_error(
        self,
        exc: GitCommandError,
        work: Repo,
        repo: RepoModel,
        base: str,
        head: str,
        *,
        op: str,
        conflict_markers: tuple[str, ...],
    ) -> None:
        """Abort ``op`` in ``work`` and raise the errors factory's conflict/generic split —
        the classification :meth:`merge` and :meth:`rebase` share, keyed only by ``op``'s
        own abort subcommand and which text marks a real conflict."""
        text = f"{exc.stdout}\n{exc.stderr}\n{exc}"
        with contextlib.suppress(GitCommandError):
            getattr(work.git, op)("--abort")
        preposition = "into" if op == "merge" else "onto"
        if any(marker in text for marker in conflict_markers):
            raise self._errors.conflict(
                f"{op} of {head} {preposition} {base} conflicts", repo=repo.full_name, op=op
            ) from exc
        raise self._errors.from_git(
            exc, f"{op} of {head} {preposition} {base} failed", repo=repo.full_name, op=op
        ) from exc
