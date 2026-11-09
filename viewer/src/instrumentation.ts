// On the server's start: uploads that a restart of the computer cut off carry on (src/server/upload.ts).
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { resumeUploads } = await import("./server/upload");
  await resumeUploads();
}
