"""
A conversation's versions, as ChatGPT keeps them: editing a message, or
asking for an answer again, keeps the old one too, so the conversation is a
tree. The page shows one path through it, from the first message to the
newest, with "‹ 2/3 ›" on each message that has other versions.

Every message has a place, (branch, position). Branch 0 is the conversation
as it was first written; a new version starts a new branch at its position,
continuing from the message before it on the path (its parent). A branch's
messages have consecutive positions, so the path is a few segments, each
running from its start to the next one's, the last to the newest message:

    branch 0:  1 2 3 4 5 6           six messages
    branch 1:        4 5             message 4 edited: branch 1 continues from (0, 3)
    path:      (0, 1), (1, 4)        shows 0:1 0:2 0:3 1:4 1:5; at 4, version 2 of 2

Reading a page of the path is one query per segment (repository.py), and
showing another version changes only the conversation's META. Switching to a
version follows its newest versions from there on.

Nothing here reads or writes: models.Conversation stores a Tree as
`path`, `branches` and `message_count` (Tree.load, Tree.dump).
"""
import hashlib
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

Node = tuple[int, int]   # where a message is: (branch, position)


@dataclass(frozen=True)
class Branch:
    parent: Node | None   # the message it continues from; None: it starts the conversation
    end: int              # its last position (start - 1 while it has no message)

    @property
    def start(self) -> int:
        return self.parent[1] + 1 if self.parent else 1


class Tree:
    """Every version of a conversation, and the path it shows. Changes make a new Tree."""

    def __init__(self, path: tuple[Node, ...], branches: dict[int, Branch]):
        self.path = path            # (branch, start) of each segment, oldest first
        self.branches = branches    # by number; branch 0 is the conversation as first written
        self._starts = [start for _, start in path]
        # The versions that continue from each message (branches other than
        # 0, oldest first), and where each branch has such messages.
        self._kids: dict[Node | None, list[int]] = {}
        for number, branch in sorted(branches.items()):
            if number:
                self._kids.setdefault(branch.parent, []).append(number)
        self._points: dict[int, list[int]] = {}
        for parent in self._kids:
            if parent is not None:
                self._points.setdefault(parent[0], []).append(parent[1])
        for points in self._points.values():
            points.sort()

    @classmethod
    def load(cls, path: list | None, branches: dict | None, count: int) -> "Tree":
        """
        As a conversation's META stores it, with message_count, the path's
        length. A conversation saved before it had versions is branch 0.
        """
        stored = {int(number): Branch(tuple(int(n) for n in branch["parent"]) if branch.get("parent") else None,
                                      int(branch["end"]))
                  for number, branch in (branches or {}).items()}
        segments = tuple((int(number), int(start)) for number, start in path) if path else ((0, 1),)
        tail = segments[-1][0]
        # The path's last branch ends at its newest message, whatever was stored for it.
        stored[tail] = Branch(stored[tail].parent if tail in stored else None, count)
        return cls(segments, stored)

    def dump(self) -> tuple[list[list[int]], dict[str, dict]]:
        """(path, branches) as META stores them (DynamoDB map keys are strings)."""
        return ([list(segment) for segment in self.path],
                {str(number): {"parent": list(branch.parent) if branch.parent else None, "end": branch.end}
                 for number, branch in self.branches.items()})

    @property
    def count(self) -> int:
        """The path's length: the conversation's message_count."""
        return self.branches[self.path[-1][0]].end

    def node(self, position: int) -> Node:
        """Where the path's message at `position` is stored."""
        return self.path[bisect_right(self._starts, position) - 1][0], position

    def segments(self, first: int, last: int) -> list[tuple[int, int, int]]:
        """(branch, from, to) for the path's positions first to last, oldest first."""
        found = []
        for i, (branch, start) in enumerate(self.path):
            stop = self.path[i + 1][1] - 1 if i + 1 < len(self.path) else self.count
            if max(first, start) <= min(last, stop):
                found.append((branch, max(first, start), min(last, stop)))
        return found

    def versions(self, first: int, last: int) -> dict[int, tuple[int, int]]:
        """
        For each of the path's positions first to last whose message has other
        versions: which one it is, and of how many ((index from 1, count)).
        """
        found = {}
        for position in range(max(first, 1), min(last, self.count) + 1):
            parent = self.node(position - 1) if position > 1 else None
            siblings = self._children(parent) if parent in self._kids else []
            if len(siblings) > 1:
                found[position] = (siblings.index(self.node(position)) + 1, len(siblings))
        return found

    def branched(self, position: int) -> "Tree":
        """
        A new version at `position`, continuing from the path's message before
        it: the path ends there now, in a new branch with no message yet
        (count = position - 1). The old version and what came after it stay.
        """
        parent = self.node(position - 1) if position > 1 else None
        number = max(self.branches) + 1
        prefix = tuple(segment for segment in self.path if segment[1] < position)
        return Tree((*prefix, (number, position)), {**self.branches, number: Branch(parent, position - 1)})

    def grown(self, messages: int) -> "Tree":
        """The path with `messages` more at its end."""
        tail = self.path[-1][0]
        return Tree(self.path, {**self.branches, tail: Branch(self.branches[tail].parent, self.count + messages)})

    def showing(self, position: int, version: int) -> "Tree":
        """
        The path through version `version` (from 1) of its message at
        `position`, then on through the newest versions after it. Raises
        IndexError if there is no such version.
        """
        siblings = self._children(self.node(position - 1) if position > 1 else None)
        if not 1 <= version <= len(siblings):
            raise IndexError(version)
        branch, _ = siblings[version - 1]
        prefix = tuple(segment for segment in self.path if segment[1] < position)
        if not prefix or prefix[-1][0] != branch:   # else it's the same branch, going on
            prefix = (*prefix, (branch, position))
        return Tree(self._onward(prefix, (branch, position)), self.branches)

    @property
    def empty_tail(self) -> bool:
        """The path ends in a version that has no message yet: an answer asked for again, still to come."""
        branch, start = self.path[-1]
        return self.branches[branch].end < start

    def without_empty_tail(self) -> "Tree":
        """
        An answer asked for again that came to nothing: its empty version
        goes, and the path shows the newest other one, and on from there.
        """
        if not self.empty_tail or len(self.path) == 1:
            return self
        branch, _ = self.path[-1]
        branches = {number: kept for number, kept in self.branches.items() if number != branch}
        rest = Tree(self.path[:-1], branches)
        return Tree(rest._onward(self.path[:-1], self.branches[branch].parent), branches)

    def fingerprint(self, before: int) -> str:
        """Which messages the path has before `before`: whether a page read back from there is still this path's."""
        segments = ".".join(f"{branch}:{start}-{end}" for branch, start, end in self.segments(1, before - 1))
        return hashlib.sha256(segments.encode()).hexdigest()[:16]

    def _children(self, parent: Node | None) -> list[Node]:
        """The versions that continue from `parent` (None: the first message's), oldest first."""
        first = (0, 1) if parent is None else (parent[0], parent[1] + 1)
        kids = [first] if self.branches[first[0]].end >= first[1] else []
        return kids + [(number, self.branches[number].start) for number in self._kids.get(parent, [])]

    def _onward(self, path: tuple[Node, ...], node: Node) -> tuple[Node, ...]:
        """`path`, which ends at `node`, on to its end along the newest version at each turn."""
        branch, position = node
        while True:
            points = self._points.get(branch, [])
            i = bisect_left(points, position)
            if i == len(points) or points[i] > self.branches[branch].end:
                return path
            at = points[i]
            branch, position = self._kids[branch, at][-1], at + 1
            path = (*path, (branch, position))
