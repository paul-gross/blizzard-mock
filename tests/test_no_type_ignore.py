"""Guard: no repo-owned Python carries a `type: ignore` comment or a weakening `# pyright:` header.

`enableTypeIgnoreComments = false` makes pyright ignore such a comment, but a comment on an error-free line stays
silent — so this scan is what rejects it. The scope matches `[tool.pyright]`: every `.py` outside `__pycache__` and
dot-directories.
"""

import io
import re
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN = re.compile(r"#\s*(?:type:\s*ignore|pyright:\s*(?:basic|standard)\b)")


def owned_python(root: Path) -> list[Path]:
    """Every `.py` under `root` that `[tool.pyright]` analyzes: none sits in `__pycache__` or a dot-directory."""
    return sorted(
        path
        for path in root.rglob("*.py")
        if not any(part == "__pycache__" or part.startswith(".") for part in path.relative_to(root).parts)
    )


def forbidden_comments(source: str) -> list[int]:
    """Line numbers of comments that suppress or weaken the type check; string literals are not comments."""
    return [
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and _FORBIDDEN.search(token.string)
    ]


def test_no_repo_python_suppresses_or_weakens_the_type_check() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{line}"
        for path in owned_python(ROOT)
        for line in forbidden_comments(path.read_text())
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "source",
    [
        "value = 1  # type: ignore\n",
        "value = 1  # type: ignore[arg-type]\n",
        "# pyright: basic\n",
        "# pyright: standard\n",
    ],
)
def test_the_scan_flags_a_comment(source: str) -> None:
    assert forbidden_comments(source) == [1]


def test_the_scan_ignores_the_same_text_in_a_string_literal() -> None:
    assert forbidden_comments('pattern = "# type: ignore"\n') == []


def test_the_scan_keeps_a_named_pyright_ignore() -> None:
    assert forbidden_comments("import x  # pyright: ignore[reportMissingTypeStubs]  # ships no py.typed\n") == []


def test_the_scan_skips_cache_and_dot_directories(tmp_path: Path) -> None:
    for relative in (
        "kept.py",
        "pkg/kept.py",
        "__pycache__/skipped.py",
        ".venv/lib/skipped.py",
        "pkg/.hidden/skipped.py",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
    assert [path.relative_to(tmp_path).as_posix() for path in owned_python(tmp_path)] == ["kept.py", "pkg/kept.py"]
