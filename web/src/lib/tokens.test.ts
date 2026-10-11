/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
// @ts-expect-error -- plain JS config, no type declarations
import tailwind from '../../tailwind.config.js'

// design/tokens.json is the source of truth; tailwind.config.js mirrors it because web/ may be
// built with a project root that cannot import ../design. This test is the drift gate.
const tokens = JSON.parse(readFileSync(resolve(process.cwd(), '../design/tokens.json'), 'utf8'))
const colors = tailwind.theme.extend.colors

describe('tailwind brand colors mirror design/tokens.json', () => {
  const up = (s: string) => s.toUpperCase()
  it('base palette', () => {
    expect(up(colors.ink.DEFAULT)).toBe(up(tokens.color.ink))
    expect(up(colors.saffron.DEFAULT)).toBe(up(tokens.color.saffron))
    expect(up(colors.ember.DEFAULT)).toBe(up(tokens.color.ember))
    expect(up(colors.cream.DEFAULT)).toBe(up(tokens.color.cream))
  })
  it('derived tints', () => {
    const d = tokens.derived
    expect(up(colors.ink.soft)).toBe(up(d.inkSoft))
    expect(up(colors.ink.rule)).toBe(up(d.inkRule))
    expect(up(colors.cream.deep)).toBe(up(d.creamDeep))
    expect(up(colors.cream.soft)).toBe(up(d.creamSoft))
    expect(up(colors.rule)).toBe(up(d.rule))
    expect(up(colors.saffron.tint)).toBe(up(d.saffronTint))
    expect(up(colors.ember.tint)).toBe(up(d.emberTint))
  })
})
