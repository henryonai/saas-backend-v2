"""
A conversation's versions (versions.py): what an edit or an answer asked for
again keeps, which path the page shows, and where each message is stored.
"""
import pytest

from assistant.versions import Branch, Tree


def shown(tree: Tree) -> list[tuple[int, int]]:
    """Where each of the path's messages is: (branch, position)."""
    return [tree.node(position) for position in range(1, tree.count + 1)]


def six() -> Tree:
    """A conversation of six messages, as saved before it had versions."""
    return Tree.load(None, None, 6)


def test_a_conversation_without_versions_is_one_branch():
    tree = six()
    assert shown(tree) == [(0, n) for n in range(1, 7)]
    assert tree.segments(2, 5) == [(0, 2, 5)] and tree.versions(1, 6) == {}
    assert Tree.load(*tree.dump(), tree.count).dump() == tree.dump()


def test_an_edit_starts_a_version_continuing_from_the_message_before():
    tree = six().branched(4).grown(2)   # message 4 edited, and answered

    assert shown(tree) == [(0, 1), (0, 2), (0, 3), (1, 4), (1, 5)]
    assert tree.branches[1] == Branch(parent=(0, 3), end=5) and tree.branches[0].end == 6   # the old one stays
    assert tree.versions(1, 5) == {4: (2, 2)}
    assert tree.segments(3, 5) == [(0, 3, 3), (1, 4, 5)]


def test_editing_the_first_message():
    tree = six().branched(1).grown(1)
    assert shown(tree) == [(1, 1)] and tree.versions(1, 1) == {1: (2, 2)}
    assert shown(tree.showing(1, 1)) == [(0, n) for n in range(1, 7)]


def test_showing_another_version_follows_the_newest_versions_after_it():
    tree = six().branched(4).grown(3).branched(6).grown(1)   # 4 edited, then 6 of that version
    assert shown(tree) == [(0, 1), (0, 2), (0, 3), (1, 4), (1, 5), (2, 6)]

    first = tree.showing(4, 1)
    assert shown(first) == [(0, n) for n in range(1, 7)] and first.versions(1, 6) == {4: (1, 2)}
    second = first.showing(4, 2)   # back: through the newest version of 6, as before
    assert shown(second) == shown(tree) and second.versions(1, 6) == {4: (2, 2), 6: (2, 2)}
    assert shown(second.showing(6, 1)) == [(0, 1), (0, 2), (0, 3), (1, 4), (1, 5), (1, 6)]
    with pytest.raises(IndexError):
        tree.showing(4, 3)


def test_a_conversation_grows_from_the_version_shown():
    tree = six().branched(4).grown(2).showing(4, 1).grown(2)   # back to the first version, then two more

    assert shown(tree)[-3:] == [(0, 6), (0, 7), (0, 8)]
    assert shown(tree.showing(4, 2)) == [(0, 1), (0, 2), (0, 3), (1, 4), (1, 5)]
    assert shown(tree.showing(4, 2).showing(4, 1))[-1] == (0, 8)


def test_an_answer_asked_for_again_is_a_version_of_the_answer():
    tree = six().branched(3)   # the answer at 3 asked for again: its message 2 stays
    assert tree.count == 2 and tree.empty_tail and tree.versions(1, 2) == {}

    answered = tree.grown(1)
    assert not answered.empty_tail and answered.versions(1, 3) == {3: (2, 2)}


def test_an_answer_asked_for_again_that_never_came_leaves_the_old_one():
    twice = six().branched(3).grown(1)
    tree = twice.showing(3, 1).branched(3)   # the first answer shown, and asked for again: nothing came

    restored = tree.without_empty_tail()

    assert 2 not in restored.branches and shown(restored) == shown(twice)   # the newest other
    assert restored.versions(3, 3) == {3: (2, 2)}
    assert six().without_empty_tail().dump() == six().dump()


def test_a_page_read_back_knows_whether_the_path_before_it_changed():
    tree = six()
    grown = tree.grown(4)
    assert grown.fingerprint(4) == tree.fingerprint(4)   # newer messages don't matter
    assert tree.branched(5).fingerprint(4) == tree.fingerprint(4)
    assert tree.branched(3).grown(1).fingerprint(4) != tree.fingerprint(4)
