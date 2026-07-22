import { Link } from "react-router-dom";
import { facetExplorePath, type FacetRef } from "../facet/registry";

/** Clickable computed-facet chip — dead text is banned for shown bricks. */
export function FacetChip(props: { facet: FacetRef; title?: string }) {
  const { facet } = props;
  return (
    <Link
      className="facet-chip"
      to={facetExplorePath(facet)}
      title={props.title ?? `探索同 facet 宇宙：${facet.label}`}
    >
      {facet.label}
    </Link>
  );
}

export function FacetChipRow(props: { facets: FacetRef[]; emptyHint?: string }) {
  if (!props.facets.length) {
    return props.emptyHint ? <span className="muted">{props.emptyHint}</span> : null;
  }
  return (
    <div className="facet-chip-row" role="list">
      {props.facets.map((f) => (
        <FacetChip key={`${f.kind}:${f.value}`} facet={f} />
      ))}
    </div>
  );
}
