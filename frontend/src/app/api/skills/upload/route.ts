import http from "node:http";
import https from "node:https";
import { Readable } from "node:stream";
import type { ReadableStream as NodeReadableStream } from "node:stream/web";

import type { NextRequest } from "next/server";

export const runtime = "nodejs";

const DEFAULT_GATEWAY_BASE_URL = "http://127.0.0.1:8001";
const MAX_SKILL_ARCHIVE_UPLOAD_BYTES = 100 * 1024 * 1024;
const MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024;
const MAX_SKILL_ARCHIVE_REQUEST_BYTES =
  MAX_SKILL_ARCHIVE_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES;
const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-length",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

function getGatewayBaseURL() {
  const candidates = [
    process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL,
    process.env.NEXT_PUBLIC_BACKEND_BASE_URL,
  ];
  const configured =
    candidates.map((value) => value?.trim()).find(Boolean) ??
    DEFAULT_GATEWAY_BASE_URL;
  return configured.replace(/\/+$/, "");
}

function buildGatewayURL(request: NextRequest) {
  const url = new URL("/api/skills/upload", getGatewayBaseURL());
  url.search = request.nextUrl.search;
  return url;
}

function parseContentLength(value: string | null): number | null {
  if (!value) return null;
  const size = Number(value);
  return Number.isFinite(size) && size >= 0 ? size : null;
}

function jsonError(status: number, detail: string) {
  return Response.json({ detail }, { status });
}

function requestHeadersToUpstreamHeaders(request: NextRequest) {
  const headers: http.OutgoingHttpHeaders = {};
  request.headers.forEach((value, key) => {
    const normalizedKey = key.toLowerCase();
    if (normalizedKey === "host" || HOP_BY_HOP_HEADERS.has(normalizedKey)) {
      return;
    }
    headers[key] = value;
  });
  return headers;
}

function responseHeadersFromUpstream(
  upstreamHeaders: http.IncomingHttpHeaders,
) {
  const headers = new Headers();
  for (const [key, value] of Object.entries(upstreamHeaders)) {
    if (HOP_BY_HOP_HEADERS.has(key.toLowerCase())) continue;
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        headers.append(key, item);
      }
      continue;
    }
    headers.set(key, value);
  }
  return headers;
}

async function proxyUploadToGateway(request: NextRequest) {
  const gatewayURL = buildGatewayURL(request);
  const transport = gatewayURL.protocol === "https:" ? https : http;

  return await new Promise<Response>((resolve, reject) => {
    const upstreamRequest = transport.request(
      gatewayURL,
      {
        method: "POST",
        headers: requestHeadersToUpstreamHeaders(request),
        timeout: 0,
      },
      (upstreamResponse) => {
        const body = Readable.toWeb(upstreamResponse) as ReadableStream;
        resolve(
          new Response(body, {
            status: upstreamResponse.statusCode ?? 502,
            statusText: upstreamResponse.statusMessage,
            headers: responseHeadersFromUpstream(upstreamResponse.headers),
          }),
        );
      },
    );

    upstreamRequest.on("error", reject);
    request.signal.addEventListener(
      "abort",
      () => {
        upstreamRequest.destroy(new Error("Client aborted upload"));
      },
      { once: true },
    );

    const body = Readable.fromWeb(
      request.body as unknown as NodeReadableStream<Uint8Array>,
    );
    body.on("error", reject);
    body.pipe(upstreamRequest);
  });
}

export async function POST(request: NextRequest) {
  const contentLength = parseContentLength(
    request.headers.get("content-length"),
  );
  if (
    contentLength !== null &&
    contentLength > MAX_SKILL_ARCHIVE_REQUEST_BYTES
  ) {
    return jsonError(413, "Skill archive too large: maximum is 100 MiB");
  }

  if (!request.body) {
    return jsonError(400, "No upload body provided");
  }

  return proxyUploadToGateway(request);
}
