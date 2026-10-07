// Mirrors app/api/shopify_auth.py _SHOP_RE. The server re-validates; this is only for fast
// feedback in the connect form. Kept dependency-free so node --test can run it (tests/).
const SHOP_RE = /^[a-z0-9][a-z0-9-]*\.myshopify\.com$/

export function normalizeShop(input: string): string {
  return input.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '')
}

export function isValidShop(input: string): boolean {
  return SHOP_RE.test(normalizeShop(input))
}
