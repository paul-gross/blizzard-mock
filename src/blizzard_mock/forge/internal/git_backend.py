"""GitPython adapter for the bare-repo backing model (``IWriteGitBackend``).

All git usage is confined to this file. The forge fronts a directory of bare
repos; this adapter resolves ``owner/name`` to one, reads refs, computes
mergeability with ``git merge-tree``, and performs a *real* merge into the base
branch via a throwaway linked worktree — the single git truth the fixture
workspace shares.

Repo resolution is permissive by design (the fixture-workspace scaffold, built
in parallel, owns the on-disk names): for ``owner/name`` it accepts
``<dir>/owner/name(.git)``, ``<dir>/name(.git)``, or ``<dir>/owner__name(.git)``,
first valid git repo wins.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
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
        git = self._open(repo.owner, repo.name)
        head_sha = self.resolve_ref(repo, head)
        tmp = Path(tempfile.mkdtemp(prefix="forge-merge-"))
        try:
            git.git.worktree("add", "--force", "--checkout", str(tmp), base)
            work = Repo(tmp)
            # The bare repo carries no committer identity; supply the forge's own
            # so the merge commit can be created (env-scoped to this call only).
            with work.git.custom_environment(**_MERGE_IDENTITY):
                try:
                    work.git.merge("--no-ff", "-m", message, head_sha)
                except GitCommandError as exc:
                    self._raise_merge_error(exc, work, repo, base, head)
                return work.git.rev_parse("HEAD")
        finally:
            with contextlib.suppress(GitCommandError):
                git.git.worktree("remove", "--force", str(tmp))
            shutil.rmtree(tmp, ignore_errors=True)

    def update_ref(self, repo: RepoModel, ref: str, sha: str) -> None:
        git = self._open(repo.owner, repo.name)
        git.git.update_ref(f"refs/heads/{ref}", sha)

    def _raise_merge_error(self, exc: GitCommandError, work: Repo, repo: RepoModel, base: str, head: str) -> None:
        text = f"{exc.stdout}\n{exc.stderr}\n{exc}"
        with contextlib.suppress(GitCommandError):
            work.git.merge("--abort")
        if "CONFLICT" in text or "Automatic merge failed" in text:
            raise self._errors.conflict(
                f"merge of {head} into {base} conflicts", repo=repo.full_name, op="merge"
            ) from exc
        raise self._errors.from_git(
            exc, f"merge of {head} into {base} failed", repo=repo.full_name, op="merge"
        ) from exc
