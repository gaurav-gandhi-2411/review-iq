// sessionStorage key holding the OAuth `state` returned by /auth/shopify/begin, so the callback
// page can reject a redirect that did not start in this browser session.
export const SHOPIFY_STATE_KEY = 'shopify_oauth_state'
