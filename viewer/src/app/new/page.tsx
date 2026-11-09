import { connection } from "next/server";
import { Suspense } from "react";
import os from "node:os";
import { NewProject } from "@/components/NewProject";
import { TopBar } from "@/components/TopBar";
import { ROOTS } from "@/server/camrig";

export const metadata = { title: "New project" };

export default function NewPage() {
  return (
    <div className="shell">
      <TopBar crumbs={[{ label: "Library", href: "/" }, { label: "New project" }]} tab="library" singleHref={null} multiHref={null} />
      <main className="page" data-screen="new">
        <header className="page-title">
          <div className="eyebrow">New project</div>
          <h1>A session from a folder of recordings</h1>
          <p className="lead" style={{ marginTop: 8 }}>Choose the folder that holds one subfolder per camera. A subfolder with .bag recordings is the depth camera; one with videos is an action camera. The recordings stay where they are and are only read.</p>
        </header>
        <Suspense fallback={<div className="card" style={{ height: 300 }} />}><Body /></Suspense>
      </main>
    </div>
  );
}

async function Body() {
  await connection();
  return <NewProject root={ROOTS[0]} home={os.homedir()} />;
}
