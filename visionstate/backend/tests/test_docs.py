"""The docs stay true to the code: every link, repository path and code name they mention exists.

Runs on the repository checkout (CI); skipped where the docs are not around (the app image).
A failure here means a rename or a removal left a doc behind: fix the doc in the same change.
"""

import re
from pathlib import Path

import pytest

from visionstate import settings

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "visionstate" / "backend" / "visionstate"
FRONTEND = REPO / "visionstate" / "frontend"

pytestmark = pytest.mark.skipif(not (REPO / "CLAUDE.md").exists(), reason="no repository checkout")

# The CHANGELOG is history: it describes each version as it was.
DOCS = [
    *REPO.glob("*.md"),
    *(REPO / "docs").glob("*.md"),
    REPO / "visionstate" / "DOCS.md",
    REPO / "visionstate" / "README.md",
    REPO / "tools" / "wheelreader" / "README.md",
    REPO / "visionstate" / "backend" / "tests" / "assets" / "README.md",
]
# Where a path in a doc may start from.
BASES = [
    REPO,
    REPO / "visionstate",
    REPO / "visionstate" / "backend",
    BACKEND,
    FRONTEND,
    FRONTEND / "src",
    FRONTEND / "src" / "lib",
]
SOURCE_SUFFIXES = {".py", ".ts", ".svelte", ".md", ".yml", ".yaml", ".sh", ".css", ".toml"}
TOP_DIRS = {"visionstate", "docs", "scripts", "tools", ".github", "backend", "frontend", "e2e", "tests", "lib",
            "pages", "engine", "api"}  # fmt: skip
# Paths that are made at run time, not kept in the repository.
NOT_IN_REPO = (".venv", "dist", "node_modules", "test-results", "dev/", "VisionStateLocal", "models")
FILE_WORDS = {"json", "py", "md", "ts", "yml", "yaml", "txt", "zip", "jpg", "png", "onnx", "db", "svelte", "css",
              "sh", "pt", "spec"}  # fmt: skip
SKIP_TREES = ("node_modules", ".venv", "dist", "test-results", "__pycache__", ".git")


def _files() -> list[Path]:
    return [p for p in REPO.rglob("*") if p.is_file() and not any(part in SKIP_TREES for part in p.parts)]


FILES = _files()
NAMES = {p.name for p in FILES}
MODULES = {p.stem: p for p in BACKEND.rglob("*.py") if "__pycache__" not in p.parts and p.stem != "__init__"}
BACKEND_TEXT = "\n".join(p.read_text(encoding="utf-8") for p in MODULES.values())
# Tables whose columns a doc may name (`prediction.read_ok`); "sensor" is also a Home Assistant domain.
TABLES = set(re.findall(r'__tablename__ = "(\w+)"', (BACKEND / "db.py").read_text(encoding="utf-8"))) - {"sensor"}
# A plan names code that is still to be written; only its links and paths are checked.
PLANS = {REPO / "docs" / "WHEEL_READER_PLAN.md"}


def _prose(doc: Path) -> str:
    """The doc without fenced code blocks (commands and examples, not references)."""
    return re.sub(r"^```.*?^```", "", doc.read_text(encoding="utf-8"), flags=re.M | re.S)


def _defines(text: str, name: str) -> bool:
    n = re.escape(name)
    return re.search(rf"(def|class)\s+{n}\b|^\s*{n}\s*[:=]|self\.{n}\s*[:=]|^\s*{n}\s*$", text, re.M) is not None


def _settings_dicts() -> dict[str, dict]:
    return {k: v for k, v in vars(settings).items() if k.isupper() and isinstance(v, dict)}


def _problem(token: str, doc: Path) -> str | None:
    """Why `token` (inline code in `doc`) does not match the repository, or None."""
    if any(c in token for c in " <>{}*…?#:,|$=") or token.startswith(("/", "http", "-")):
        return None
    # settings.X["key"] / X["key"]
    m = re.fullmatch(r'(?:settings\.)?([A-Z][A-Z0-9_]+)\["(\w+)"\]', token)
    if m:
        d = getattr(settings, m[1], None)
        if not isinstance(d, dict):
            return f"settings.{m[1]} is not a dict of settings" if token.startswith("settings.") else None
        return None if m[2] in d else f'settings.{m[1]} has no "{m[2]}"'
    # module.name, Class.name, table.column
    m = re.fullmatch(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)(\(\))?", token)
    if m and m[2] not in FILE_WORDS:
        if doc in PLANS:
            return None
        first, name = m[1], m[2]
        if first in MODULES:
            text = MODULES[first].read_text(encoding="utf-8")
            if _defines(text, name) or name.endswith("_"):
                return None
            return f"{first}.py does not define {name}"
        if first[0].isupper() and re.search(rf"class {first}\b", BACKEND_TEXT):
            return None if _defines(BACKEND_TEXT, name) else f"nothing defines {first}.{name}"
        if first in TABLES:
            return None if _defines((BACKEND / "db.py").read_text(encoding="utf-8"), name) else f"no column {token}"
        return None
    # paths
    if not re.fullmatch(r"[\w.@-]+(/[\w.@-]+)*/?", token) or any(n in token for n in NOT_IN_REPO):
        return None
    path = token.rstrip("/")
    has_dir = "/" in path
    if has_dir and path.split("/")[0] not in TOP_DIRS and not path.startswith(".."):
        return None
    if not has_dir and Path(path).suffix not in SOURCE_SUFFIXES:
        return None
    if not has_dir:
        return None if path in NAMES else f"no file named {path}"
    for base in [doc.parent, *BASES]:
        if (base / path).exists():
            return None
    return f"no such path {token}"


def test_links_in_the_docs_lead_somewhere():
    missing = []
    for doc in DOCS:
        for target in re.findall(r"\]\(([^)\s]+)\)", _prose(doc)):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (doc.parent / target.split("#")[0]).exists():
                missing.append(f"{doc.relative_to(REPO)}: {target}")
    assert not missing, "links to nothing:\n" + "\n".join(missing)


def test_code_named_in_the_docs_exists():
    problems = []
    for doc in DOCS:
        for token in re.findall(r"`([^`\n]+)`", _prose(doc)):
            why = _problem(token.strip(), doc)
            if why:
                problems.append(f"{doc.relative_to(REPO)}: `{token}` — {why}")
    assert not problems, "the docs name things the code does not have:\n" + "\n".join(problems)


def test_values_named_in_the_docs_match_settings():
    """`some_setting` (0.88) in a doc must be the value in settings.py."""
    values: dict[str, set] = {}
    for d in _settings_dicts().values():
        for k, v in d.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                values.setdefault(k, set()).add(float(v))
    wrong = []
    for doc in DOCS:
        pattern = r'`(?:[A-Z_]+\[")?(\w+)(?:"\])?` \((\d+(?:\.\d+)?)[ ),;]'
        for key, number in re.findall(pattern, _prose(doc)):
            if len(values.get(key, ())) == 1 and float(number) not in values[key]:
                wrong.append(f"{doc.relative_to(REPO)}: `{key}` ({number}) — settings.py says {values[key].pop():g}")
    assert not wrong, "values in the docs differ from settings.py:\n" + "\n".join(wrong)
