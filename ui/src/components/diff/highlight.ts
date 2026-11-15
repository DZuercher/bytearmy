// Syntax highlighting of the lines of a diff. Each side of a hunk is highlighted as one
// block, so strings and comments over several lines come out right, and then cut into lines.
import type { Element, ElementContent, Root } from 'hast'
import { common, createLowlight } from 'lowlight'
import type { DiffFile } from '@/api/client'

const lowlight = createLowlight(common)

// A piece of a line and the highlight.js classes it has, e.g. "hljs-string".
export type Token = { text: string; className: string }

// File names and extensions the grammars do not know by their alias.
const NAMES: Record<string, string> = { Dockerfile: 'dockerfile', Makefile: 'makefile' }
const EXTENSIONS: Record<string, string> = { mjs: 'javascript', cjs: 'javascript', jsonc: 'json', svelte: 'xml', vue: 'xml' }

function language(path: string): string | null {
  const name = path.split('/').pop() ?? ''
  if (NAMES[name] && lowlight.registered(NAMES[name])) return NAMES[name]
  const extension = name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''
  const guess = EXTENSIONS[extension] ?? extension
  return guess && lowlight.registered(guess) ? guess : null
}

// The tokens of each line of each hunk, in the order of `hunk.lines`. Context lines take
// the new side. Without a known language every line is one plain token.
export function highlightFile(file: DiffFile): Token[][][] {
  const lang = language(file.path)
  return file.hunks.map((hunk) => {
    const plain = hunk.lines.map((line) => [{ text: line.text, className: '' }])
    if (!lang) return plain
    const old = hunk.lines.filter((line) => line.kind !== 'add')
    const neu = hunk.lines.filter((line) => line.kind !== 'delete')
    const oldTokens = highlightBlock(lang, old.map((line) => line.text))
    const newTokens = highlightBlock(lang, neu.map((line) => line.text))
    let o = 0
    let n = 0
    return hunk.lines.map((line) => {
      if (line.kind === 'delete') return oldTokens[o++]
      if (line.kind === 'add') return newTokens[n++]
      o++
      return newTokens[n++]
    })
  })
}

function highlightBlock(lang: string, lines: string[]): Token[][] {
  let tree: Root
  try {
    tree = lowlight.highlight(lang, lines.join('\n'))
  } catch {
    return lines.map((text) => [{ text, className: '' }])
  }
  const result: Token[][] = [[]]
  const walk = (nodes: (ElementContent | Root['children'][number])[], classes: string[]) => {
    for (const node of nodes) {
      if (node.type === 'text') {
        node.value.split('\n').forEach((part, i) => {
          if (i > 0) result.push([])
          if (part) result[result.length - 1].push({ text: part, className: classes.join(' ') })
        })
      } else if (node.type === 'element') {
        walk(node.children, [...classes, ...classNames(node)])
      }
    }
  }
  walk(tree.children, [])
  // The highlighter keeps every line break, so the line count matches; pad just in case.
  while (result.length < lines.length) result.push([])
  return result
}

function classNames(node: Element): string[] {
  const value = node.properties?.className
  return Array.isArray(value) ? value.map(String) : typeof value === 'string' ? [value] : []
}
