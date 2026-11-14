"""The layout of a workspace: a tree of splits whose leaves are pane ids.

Every function here is pure: it returns a new tree and leaves its argument alone.
"""

from __future__ import annotations

from bytearmy.models import Split

Tree = Split | str | None


def leaves(tree: Tree) -> list[str]:
    """The pane ids in the tree, left to right and top to bottom."""
    if tree is None:
        return []
    if isinstance(tree, str):
        return [tree]
    return [leaf for child in tree.children for leaf in leaves(child)]


def add_leaf(tree: Tree, new: str, beside: str | None = None) -> Split | str:
    """Split the pane `beside` and put `new` into the second half.

    The split runs across the split that holds `beside`, so repeated calls spiral
    inwards the way a tiling window manager does. Without a usable `beside` the last
    pane is split.
    """
    existing = leaves(tree)
    if not existing:
        return new
    target = beside if beside in existing else existing[-1]

    def place(node: Split | str, around: str) -> Split | str:
        if node == target:
            return Split(direction="row" if around == "column" else "column", children=[node, new])
        if isinstance(node, str):
            return node
        children = [place(child, node.direction) for child in node.children]
        return node.model_copy(update={"children": children})

    return place(tree, "column")


def remove_leaf(tree: Tree, gone: str) -> Tree:
    """The tree without the pane `gone`; a split left with one child becomes that child."""
    if tree is None or tree == gone:
        return None
    if isinstance(tree, str):
        return tree
    children = [kept for child in tree.children if (kept := remove_leaf(child, gone)) is not None]
    if len(children) == 1:
        return children[0]
    if len(children) == len(tree.children):
        return tree.model_copy(update={"children": children})
    # A child is gone: the stored percentages no longer fit, fall back to equal sizes.
    return Split(direction=tree.direction, children=children)
