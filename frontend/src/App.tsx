export function App() {
  return (
    <main className="page">
      <header className="masthead">
        <a className="wordmark" href="/" aria-label="FullAura home">FullAura</a>
        <span className="status">Early prototype</span>
      </header>
      <section className="intro" aria-labelledby="title">
        <p className="eyebrow">Personal style, considered together</p>
        <h1 id="title">Your next look.<br />Your own terms.</h1>
        <p className="description">
          We are building a way to plan outfits around your preferences, budget,
          and the things you already own, with personal previews and shopping links.
        </p>
        <aside className="notice" aria-label="Prototype status">
          <h2>Work has just started.</h2>
          <p>
            Outfit planning, photo previews, and grooming suggestions are under
            development. This version does not collect photos or generate recommendations.
          </p>
        </aside>
      </section>
      <footer>
        <span>FullAura · 2026</span>
        <a href="https://github.com/vbabenko97/fullaura">View the project</a>
      </footer>
    </main>
  );
}
