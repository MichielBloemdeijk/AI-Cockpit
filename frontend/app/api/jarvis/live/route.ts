const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const runtime = "nodejs";

function backendOrigin(): string {
  return (process.env.AI_COCKPIT_BACKEND_URL || DEFAULT_BACKEND_ORIGIN).replace(/\/$/, "");
}

export async function GET(request: Request): Promise<Response> {
  const incomingUrl = new URL(request.url);
  const upstreamUrl = new URL("/api/jarvis/live", backendOrigin());
  upstreamUrl.search = incomingUrl.search;

  const headers = new Headers({ Accept: "text/event-stream" });
  for (const name of ["authorization", "cookie", "last-event-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const upstream = await fetch(upstreamUrl, {
    method: "GET",
    headers,
    cache: "no-store",
    signal: request.signal,
  });

  const responseHeaders = new Headers();
  responseHeaders.set(
    "Content-Type",
    upstream.headers.get("content-type") || "text/event-stream; charset=utf-8",
  );
  responseHeaders.set("Cache-Control", "no-cache, no-transform");
  responseHeaders.set("X-Accel-Buffering", "no");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}
