// Run: node --test scripts/   (Node >= 22.18 strips TypeScript types natively)
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { describeWait, parseRetryAfter } from '../src/lib/retryAfter.ts'

test('parses delta-seconds', () => {
  assert.equal(parseRetryAfter('321'), 321)
  assert.equal(parseRetryAfter(' 60 '), 60)
})

test('missing, zero, negative, fractional and HTTP-date values are null', () => {
  for (const v of [null, undefined, '', '0', '-5', '1.5', 'Wed, 21 Oct 2026 07:28:00 GMT', 'abc']) {
    assert.equal(parseRetryAfter(v), null, String(v))
  }
})

test('wait phrase is seconds under 90 s, rounded-up minutes above, generic when unknown', () => {
  assert.equal(describeWait(45), 'try again in 45 s')
  assert.equal(describeWait(321), 'try again in 6 min')
  assert.equal(describeWait(3600), 'try again in 60 min')
  assert.equal(describeWait(null), 'try again in a minute')
})
