// Pure helpers on react-mosaic's layout tree. Leaves are pane ids.
import { isSplitNode, isTabsNode } from 'react-mosaic-component'

export function addLeaf(tree, id) {
  if (tree === null) return id
  if (isSplitNode(tree) && tree.direction === 'row') {
    return { type: 'split', direction: 'row', children: [...tree.children, id] }
  }
  return { type: 'split', direction: 'row', children: [tree, id] }
}

export function removeLeaf(tree, id) {
  if (tree === null || tree === id) return null
  if (isTabsNode(tree)) {
    const tabs = tree.tabs.filter((tab) => tab !== id)
    if (tabs.length === tree.tabs.length) return tree
    if (tabs.length <= 1) return tabs[0] ?? null
    return { ...tree, tabs, activeTabIndex: Math.min(tree.activeTabIndex, tabs.length - 1) }
  }
  if (!isSplitNode(tree)) return tree
  const children = tree.children
    .map((child) => removeLeaf(child, id))
    .filter((child) => child !== null)
  if (children.length === 0) return null
  if (children.length === 1) return children[0]
  if (children.length === tree.children.length) return { ...tree, children }
  // A child is gone: the stored percentages no longer fit, fall back to equal sizes.
  return { type: 'split', direction: tree.direction, children }
}

// Takes the pane out and puts it back beside everything else, on the other axis.
export function moveToOtherAxis(tree, id) {
  const rest = removeLeaf(tree, id)
  if (rest === null) return tree
  const direction = isSplitNode(tree) && tree.direction === 'row' ? 'column' : 'row'
  return { type: 'split', direction, children: [id, rest] }
}
