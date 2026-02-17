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

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // Health check
    if (url.pathname === "/__health") {
      return new Response("ok", { status: 200 });
    }

    // Build target URL preserving path + query string
    const targetUrl = TARGET + url.pathname + url.search;

    // Forward headers, excluding ones Cloudflare should set itself
    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ray");
    headers.delete("cf-visitor");
    headers.delete("cf-worker");

    // Forward the request
    const resp = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.method !== "GET" && request.method !== "HEAD"
        ? request.body
        : undefined,
    });

    // Return response with CORS headers for flexibility
    const responseHeaders = new Headers(resp.headers);
    responseHeaders.set("X-Proxy", "cf-worker");

    return new Response(resp.body, {
      status: resp.status,
      statusText: resp.statusText,
      headers: responseHeaders,
    });
  },
};
