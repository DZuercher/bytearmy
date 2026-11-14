"""Checks for the layout tree of a workspace."""

from __future__ import annotations

from bytearmy.layout import add_leaf, leaves, remove_leaf
from bytearmy.models import Split


def row(*children, sizes=None) -> Split:
    return Split(direction="row", children=list(children), split_percentages=sizes)


def column(*children, sizes=None) -> Split:
    return Split(direction="column", children=list(children), split_percentages=sizes)


def test_the_first_pane_fills_the_workspace():
    assert add_leaf(None, "p1") == "p1"


def test_a_new_pane_splits_the_pane_it_is_opened_beside():
    tree = add_leaf("p1", "p2", beside="p1")
    assert tree == row("p1", "p2")

    tree = add_leaf(tree, "p3", beside="p2")
    assert tree == row("p1", column("p2", "p3"))

    tree = add_leaf(tree, "p4", beside="p3")
    assert tree == row("p1", column("p2", row("p3", "p4")))

    tree = add_leaf(tree, "p5", beside="p1")
    assert tree == row(column("p1", "p5"), column("p2", row("p3", "p4")))


def test_without_a_usable_neighbour_the_last_pane_is_split():
    tree = row("p1", "p2")

    assert add_leaf(tree, "p3") == row("p1", column("p2", "p3"))
    assert add_leaf(tree, "p3", beside="ghost") == row("p1", column("p2", "p3"))


def test_adding_keeps_the_sizes_the_user_set():
    tree = row("p1", "p2", sizes=[30, 70])

    assert add_leaf(tree, "p3", beside="p2") == row("p1", column("p2", "p3"), sizes=[30, 70])


def test_leaves_are_listed_in_reading_order():
    assert leaves(None) == []
    assert leaves("p1") == ["p1"]
    assert leaves(row("p3", column("p1", row("p4", "p2")))) == ["p3", "p1", "p4", "p2"]


def test_removing_a_pane_collapses_the_split_it_leaves_alone():
    tree = row("p1", column("p2", "p3"), sizes=[40, 60])

    assert remove_leaf(tree, "p3") == row("p1", "p2", sizes=[40, 60])
    assert remove_leaf(tree, "p1") == column("p2", "p3")
    assert remove_leaf(tree, "ghost") == tree
    assert remove_leaf("p1", "p1") is None
    assert remove_leaf("p1", "p2") == "p1"
    assert remove_leaf(None, "p1") is None


def test_removing_a_pane_drops_sizes_that_no_longer_fit():
    tree = row("p1", "p2", "p3", sizes=[20, 30, 50])

    assert remove_leaf(tree, "p2") == row("p1", "p3")


def test_the_tree_handed_in_is_left_alone():
    tree = row("p1", column("p2", "p3"), sizes=[40, 60])
    before = tree.model_copy(deep=True)

    add_leaf(tree, "p4", beside="p3")
    remove_leaf(tree, "p2")

    assert tree == before
