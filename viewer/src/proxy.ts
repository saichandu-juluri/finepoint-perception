// Every request must name this machine's loopback address as its Host, the pages included: a site that re-points its
// own name to 127.0.0.1 (DNS rebinding) could otherwise read the pages, and with them file names and transcripts.
import { NextResponse } from "next/server";
import { localHost } from "@/server/data";

export function proxy(req: Request) {
  return localHost(req) ? NextResponse.next() : new Response("Forbidden", { status: 403 });
}
