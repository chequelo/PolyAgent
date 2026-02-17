/**
 * Cloudflare Worker — Polymarket CLOB API Proxy
 *
 * Forwards requests to clob.polymarket.com, bypassing datacenter ASN
 * bot detection since Cloudflare-to-Cloudflare traffic gets higher trust.
 *
 * Deploy: cd cloudflare-proxy && npx wrangler deploy
 * Then set POLY_CLOB_PROXY_URL=https://polymarket-clob-proxy.<your-subdomain>.workers.dev
 */

const TARGET = "https://clob.polymarket.com";

// Only forward headers that the CLOB API needs (auth + content).
// Everything else (IP, geo, fingerprint headers) gets stripped.
const ALLOWED_HEADERS = [
  "content-type",
  "accept",
  "authorization",
  "poly_address",
  "poly_signature",
  "poly_timestamp",
  "poly_nonce",
  "poly_api_key",
];

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // Health check
    if (url.pathname === "/__health") {
      return new Response("ok", { status: 200 });
    }

    // Build target URL preserving path + query string
    const targetUrl = TARGET + url.pathname + url.search;

    // Build clean headers — only pass auth and content headers
    const cleanHeaders = new Headers();
    for (const name of ALLOWED_HEADERS) {
      const value = request.headers.get(name);
      if (value) {
        cleanHeaders.set(name, value);
      }
    }

    // Forward the request with clean headers
    const resp = await fetch(targetUrl, {
      method: request.method,
      headers: cleanHeaders,
      body: request.method !== "GET" && request.method !== "HEAD"
        ? request.body
        : undefined,
    });

    return new Response(resp.body, {
      status: resp.status,
      statusText: resp.statusText,
      headers: resp.headers,
    });
  },
};
