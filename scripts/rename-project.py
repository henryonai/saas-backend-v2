"""
Renames the project: every name this template writes into packages, stacks,
AWS resources, events and the browser. Run it once in a new copy of the
template, before its first deploy:

    uv run python scripts/rename-project.py acme
    uv run python scripts/rename-project.py acme --title "Acme" --dry-run

It reads the current name from pyproject.toml ("<name>-backend"), so it can
also rename a project again. In every file git tracks it changes:

    saas-shared, saas-<module>, saas-web    package names
    saas-dev, saas-prod                     stack names (samconfig.toml)
    project=saas                            the cost allocation tag (samconfig.toml)
    saas-${Stage}                           user pool name, metrics namespace
    saas_${Stage}_...                       AgentCore names (underscores: they allow no dashes)
    /saas/dev/payment-webhook-secret        SSM parameter names
    saas.app                                the event source (shared/events.py, rules)
    saas.theme, saas.lang, ...              browser storage keys

and, with --title, the app's name on screen (web/index.html and the locale
files). A new name also empties `artifacts` in .ship/profile.yaml: those are
the template's own links (its project page, its work log), not the copy's.
Then it refreshes uv.lock and the requirements.txt files.

Deployed resources keep their names: a renamed project deploys new stacks.
It needs a clean git working tree, so `git diff` shows exactly what it did.
"""
import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALID_NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
MAX_LENGTH = 20  # room for the longest generated names, e.g. "<name>-prod-github-deploy"
SHIP_PROFILE = ROOT / ".ship/profile.yaml"
# The top-level `artifacts:` key and the indented lines under it
ARTIFACTS = re.compile(r"^artifacts:[^\n]*\n(?:[ \t]+[^\n]*\n)*", re.M)
LOCALES = sorted((ROOT / "web/src/shared/i18n/locales").glob("*.json"))


def current_name() -> str:
    match = re.search(r'^name = "([a-z0-9-]+)-backend"$', (ROOT / "pyproject.toml").read_text(), re.M)
    if not match:
        sys.exit('pyproject.toml has no line name = "<name>-backend"; is this the template?')
    return match.group(1)


def name_rules(old: str, new: str) -> list[tuple[re.Pattern, str]]:
    o, o_ = re.escape(old), re.escape(old.replace("-", "_"))
    return [
        # The examples below say <name>, so a long new name can't push these lines over the limit.
        (re.compile(r"\b" + o + r"-backend-v2\b"), new),            # the repository, in the README
        (re.compile(r"(?<![\w])" + o + r"(?=[-.][\w$<{])"), new),   # <name>-shared, <name>.app, ${1:-<name>-dev}
        (re.compile(r"(?<=/)" + o + r"(?=/)"), new),                # /<name>/dev/...
        (re.compile(r"(?<=project=)" + o + r"(?![\w-])"), new),     # project=<name>, the cost allocation tag
        # AgentCore names allow no dashes: <name>_${Stage}_assistant
        (re.compile(r"(?<![A-Za-z0-9])" + o_ + r"(?=_[\w$<{])"), new.replace("-", "_")),
    ]


def leftover_pattern(old: str) -> re.Pattern:
    """Any old name left as a word of its own: a separate, broader search than the rules above."""
    names = "|".join(re.escape(n) for n in {old, old.replace("-", "_")})
    return re.compile(r"(?<![A-Za-z0-9])(?:" + names + r")(?![A-Za-z0-9])")


def title_rules(title: str) -> dict[Path, tuple[re.Pattern, Callable[[re.Match], str]]]:
    tag = f"<title>{html.escape(title)}</title>"
    quoted = json.dumps(title, ensure_ascii=False)
    rules = {ROOT / "web/index.html": (re.compile(r"<title>[^<]*</title>"), lambda _: tag)}
    for locale in LOCALES:  # "app": { "name": "..." }
        rules[locale] = (re.compile(r'("app":\s*\{\s*"name":\s*)"(?:[^"\\]|\\.)*"'), lambda m: m.group(1) + quoted)
    return rules


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / name for name in out.split("\0") if name and (ROOT / name).is_file()]


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None  # not a text file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="lowercase letters, digits and dashes, e.g. acme or acme-shop")
    parser.add_argument("--title", help='the app\'s name on screen, e.g. "Acme" (default: unchanged)')
    parser.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    args = parser.parse_args()

    old, new = current_name(), args.name
    if not VALID_NAME.match(new) or len(new) > MAX_LENGTH:
        sys.exit(f"{new!r}: use 1-{MAX_LENGTH} lowercase letters, digits and dashes, starting with a letter")
    if new == old and not args.title:
        sys.exit(f"The project is already called {old!r}.")
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True)
    if dirty.stdout.strip() and not args.dry_run:
        sys.exit("Commit or stash your changes first, so `git diff` shows only the rename.")

    renames = name_rules(old, new) if new != old else []
    titles = title_rules(args.title) if args.title else {}
    changed: dict[Path, int] = {}
    for path in tracked_files():
        text = read_text(path)
        if text is None:
            continue
        result, count = text, 0
        for pattern, replacement in renames:
            result, n = pattern.subn(replacement, result)
            count += n
        if path in titles:
            pattern, replacement = titles[path]
            result, n = pattern.subn(replacement, result)
            count += n
        if renames and path == SHIP_PROFILE:
            emptied = ARTIFACTS.sub("artifacts: {}\n", result)
            count += emptied != result
            result = emptied
        if count:
            changed[path] = count
            if not args.dry_run:
                path.write_text(result, encoding="utf-8")

    print(f"{'Would change' if args.dry_run else 'Changed'} {sum(changed.values())} names in {len(changed)} files:")
    for path, count in sorted(changed.items()):
        print(f"  {count:4}  {path.relative_to(ROOT)}")
    if args.dry_run or not renames:
        return

    if shutil.which("uv"):
        subprocess.run(["uv", "lock", "--quiet"], cwd=ROOT, check=True)
        subprocess.run(["./scripts/export-requirements.sh"], cwd=ROOT, check=True)
        print("Refreshed uv.lock and the requirements.txt files.")
    else:
        print("uv not found: run `uv lock` and ./scripts/export-requirements.sh yourself.")

    leftover = leftover_pattern(old)
    leftovers = [] if leftover.search(new) else [   # a new name that contains the old one can't be checked
        f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, match.start()) + 1}: {match.group(0)}"
        for path in tracked_files()
        if (text := read_text(path)) is not None
        for match in leftover.finditer(text)
    ]
    if leftovers:
        print(f"Still named {old!r} (check by hand):", *leftovers, sep="\n  ")

    # A longer name makes lines longer: list Python lines now over ruff's limit (CI checks it).
    limit = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["ruff"]["line-length"]
    too_long = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in changed if path.suffix == ".py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if len(line) > limit
    ]
    if too_long:
        print(f"Now longer than {limit} characters (wrap them):", *too_long, sep="\n  ")

    print(f"""
Next:
  uv sync --all-packages && (cd web && pnpm install)
  git diff                  # review, then commit
Before the first deploy (see "CI/CD" in the README):
  aws ssm put-parameter --name /{new}/dev/payment-webhook-secret \\
      --type SecureString --value "$(openssl rand -hex 32)"
  sam build && sam deploy --config-env dev      # creates the stack {new}-dev""")


if __name__ == "__main__":
    main()
