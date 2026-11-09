import { Icon } from "@/components/Icon";
import { NavLink } from "@/components/nav";
import { TopBar } from "@/components/TopBar";

export default function NotFound() {
  return (
    <div className="shell">
      <TopBar crumbs={[{ label: "Library", href: "/" }, { label: "Not found" }]} tab={null} singleHref={null} multiHref={null} />
      <main className="card nf" data-screen="not-found">
        <div className="art"><Icon name="camoff" /></div>
        <h1>Nothing here</h1>
        <p>There is no session or video at this address. Its folder may have been renamed or moved, or it is not in a folder the app looks in.</p>
        <NavLink className="btn primary" href="/"><Icon name="arrowL" />Back to the library</NavLink>
      </main>
    </div>
  );
}
