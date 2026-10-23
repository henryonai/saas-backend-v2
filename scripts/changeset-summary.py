"""
Describes a CloudFormation change set in Markdown: which resources a deploy
would add, modify or remove, which properties change, and which resources it
would replace. deploy.yml writes it into the summary of a run that prepares a
deploy, so you can read it before applying.

    python3 scripts/changeset-summary.py <stack> [<change set ARN>]

Without a change set it says the backend has no changes. Uses the AWS CLI and
your credentials; standard library only, so it runs on any CI runner.
"""
import json
import subprocess
import sys

ACTIONS = ("Add", "Modify", "Remove", "Import", "Dynamic")
REPLACED = {"True": "**Yes**", "Conditional": "Maybe", "False": ""}


def describe(change_set: str) -> tuple[dict, list[dict]]:
    """The change set and all its resource changes (the API pages them)."""
    first, changes, token = None, [], None
    while True:
        command = ["aws", "cloudformation", "describe-change-set", "--change-set-name", change_set, "--output", "json"]
        if token:
            command += ["--next-token", token]
        page = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
        first = first or page
        changes += [c["ResourceChange"] for c in page.get("Changes", []) if c.get("Type") == "Resource"]
        token = page.get("NextToken")
        if not token:
            return first, changes


def properties(change: dict) -> str:
    names = sorted({d["Target"].get("Name") or d["Target"]["Attribute"] for d in change.get("Details", [])})
    return ", ".join(f"`{name}`" for name in names)


def main() -> None:
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    stack = sys.argv[1]
    change_set = sys.argv[2] if len(sys.argv) == 3 else ""
    print(f"## Prepared: {stack}\n")
    if not change_set:
        print("The backend has no changes. Applying publishes the web app only.\n")
        return

    info, changes = describe(change_set)
    counts = {action: sum(c["Action"] == action for c in changes) for action in ACTIONS}
    replaced = sum(c.get("Replacement") == "True" for c in changes)
    maybe = sum(c.get("Replacement") == "Conditional" for c in changes)
    summary = ", ".join(f"{n} to {action.lower()}" for action, n in counts.items() if n)
    resources = f"{len(changes)} resource{'' if len(changes) == 1 else 's'}"
    print(f"Change set `{info['ChangeSetName']}`: {resources} ({summary}).\n")
    if replaced or maybe:
        print(f"**Replaced: {replaced}, maybe: {maybe}.** A replaced resource is created again. The tables and "
              "the user pool keep the old one (UpdateReplacePolicy), but the stack switches to a new, empty one, "
              "so check these before applying. \"Maybe\" depends on values CloudFormation only knows while "
              "deploying.\n")
    print("| Change | Resource | Type | Properties | Replaced |")
    print("|---|---|---|---|---|")
    for c in sorted(changes, key=lambda c: (ACTIONS.index(c["Action"]), c["LogicalResourceId"])):
        print(f"| {c['Action']} | `{c['LogicalResourceId']}` | {c['ResourceType']} | {properties(c)} "
              f"| {REPLACED.get(c.get('Replacement', 'False'), '')} |")
    print()


if __name__ == "__main__":
    main()
