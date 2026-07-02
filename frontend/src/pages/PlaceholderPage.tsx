export function PlaceholderPage(props: { title: string }) {
  return (
    <div className="page">
      <h1>{props.title}</h1>
      <section className="card">
        <div className="card-body">
          <div className="state-hint">占位页 — 尚未实现</div>
        </div>
      </section>
    </div>
  );
}
