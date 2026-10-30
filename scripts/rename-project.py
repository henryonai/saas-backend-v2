"""
Renames the project: every name this template writes into packages, stacks,
AWS resources, events and the browser. Run it once in a new copy of the
template, before its first deploy:

    uv run python scripts/rename-project.py acme
    uv run python scripts/rename-project.py acme --title "Acme" --dry-run
    uv run python scripts/rename-project.py acme-shop --strict   # what CI runs on every PR

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
files). Then it refreshes uv.lock and the requirements.txt files.

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


def inside_new_name(text: str, match: re.Match, new: str) -> bool:
    """Whether a match of the old name is part of the new one (renaming acme to acme-shop, say)."""
    return any(found.start() <= match.start() and match.end() <= found.end()
               for name in {new, new.replace("-", "_")}
               for found in re.finditer(re.escape(name), text))


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
    parser.add_argument("--strict", action="store_true",
                        help="exit with an error if names or overlong lines are left to fix by hand (CI uses it)")
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
    leftovers = [
        f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, match.start()) + 1}: {match.group(0)}"
        for path in tracked_files()
        if (text := read_text(path)) is not None
        for match in leftover.finditer(text)
        if not inside_new_name(text, match, new)
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
    if args.strict and (leftovers or too_long):
        sys.exit("Left to fix by hand: see above.")

    print(f"""
Next:
  uv sync --all-packages && (cd web && pnpm install)
  git diff                  # review, then commit
  LICENSE is MIT: keep its notice (add your own beside it if you like).
Then the first deploy (README, "Starting a new project from this template"):
  read -rs STRIPE_KEY && aws ssm put-parameter --name /{new}/dev/stripe-secret-key \\
      --type SecureString --value "$STRIPE_KEY"; unset STRIPE_KEY
  sam build && sam deploy --config-env dev      # creates the stack {new}-dev
  uv run python scripts/stripe-setup.py --stack {new}-dev
  (cd web && ./scripts/deploy.sh {new}-dev)
  uv run scripts/smoke-test.py --stack {new}-dev
The assistant takes a second deploy: README, step 3.""")


if __name__ == "__main__":
    main()
