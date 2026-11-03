import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isValidShop, normalizeShop } from '../src/lib/shopDomain.ts'

test('accepts a plain myshopify domain, any case, with surrounding space', () => {
  assert.equal(isValidShop('my-store.myshopify.com'), true)
  assert.equal(isValidShop('  My-Store.MyShopify.com '), true)
})

test('normalizes a pasted admin URL down to the domain', () => {
  assert.equal(normalizeShop('https://my-store.myshopify.com/admin/products'), 'my-store.myshopify.com')
  assert.equal(isValidShop('https://my-store.myshopify.com/admin'), true)
})

test('rejects non-myshopify, empty, lookalike and injection-shaped input', () => {
  for (const bad of [
    '',
    'my-store',
    'my-store.com',
    'evil.com/my-store.myshopify.com',
    'my-store.myshopify.com.evil.com',
    '-bad.myshopify.com',
    '.myshopify.com',
    'a b.myshopify.com',
    'store.myshopify.com?x=1',
  ]) {
    assert.equal(isValidShop(bad), false, bad)
  }
})
