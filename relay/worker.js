// Cloudflare Worker: transparent relay to Playtomic's public + auth endpoints.
// GitHub Actions' datacenter IPs (and most non-browser clients) get a 403 from
// Playtomic's CloudFront WAF; this forwards the request from Cloudflare's
// egress instead, which the WAF accepts. The path prefix selects the upstream
// host, so both playtomic.com and api.playtomic.io go through one Worker:
//   /api/clubs/availability , /clubs/   -> playtomic.com   (anonymous view)
//   /v3/auth/login , /v1/availability   -> api.playtomic.io (member view)
const ROUTES = [
  ["/api/clubs/availability", "playtomic.com"],
  ["/clubs/", "playtomic.com"],
  ["/v3/auth/login", "api.playtomic.io"],
  ["/v1/availability", "api.playtomic.io"],
];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const route = ROUTES.find(([p]) => url.pathname.startsWith(p));
    if (!route) return new Response("forbidden path", { status: 403 });
    // ponytail: shared-token gate, only enforced when RELAY_TOKEN secret is set
    if (env.RELAY_TOKEN && request.headers.get("X-Relay-Token") !== env.RELAY_TOKEN) {
      return new Response("unauthorized", { status: 401 });
    }
    url.hostname = route[1];
    url.protocol = "https:";
    url.port = "";
    const headers = {
      "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      "Accept": "application/json,text/html",
      "Accept-Language": "it-IT,it;q=0.9",
    };
    // Forward auth + content-type so login (POST) and member availability work.
    for (const h of ["Authorization", "Content-Type"]) {
      const v = request.headers.get(h);
      if (v) headers[h] = v;
    }
    const method = request.method;
    const upstream = await fetch(url.toString(), {
      method,
      headers,
      // read the (small) body to a string: avoids the duplex-stream requirement
      body: method === "GET" || method === "HEAD" ? undefined : await request.text(),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") || "application/json",
      },
    });
  },
};
