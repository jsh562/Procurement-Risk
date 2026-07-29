import { EXPLANATIONS, type ExplanationKey } from "./explainCopy";
import styles from "./page.module.css";

/**
 * The provenance disclosure attached to a figure.
 *
 * A native `<details>` rather than a tooltip, and the choice is not cosmetic. A
 * `title` attribute is unreachable by keyboard, announced inconsistently, and
 * gone on touch — so a figure whose only explanation lived in one would be
 * explained to some readers and not others. `<details>` is focusable, toggles on
 * Enter and Space without any script, and its content is in the accessibility
 * tree whether open or closed.
 *
 * The panel floats over the row rather than expanding it — see `.explainBody`.
 * Opening one moves nothing, which matters on a 24-row list where an inline
 * expansion would displace every row below it.
 *
 * It also keeps the row honest against FR-019 and FR-050, both of which require
 * that something be reachable *without* hover or expansion. Nothing here is the
 * sole carrier of anything: every figure these panels describe is already
 * rendered beside them, and closing every panel on the page removes no fact.
 *
 * The summary carries a visible glyph and a visually-hidden name, because an
 * icon with no accessible name is exactly what FR-050 refuses elsewhere. The
 * name is the entry's title, so each toggle on a row announces distinctly
 * instead of twenty identical "more information" buttons.
 */
/**
 * Groups every panel into one exclusive set, so opening one closes any other.
 *
 * The panels overlay the row and are positioned identically within it, so two
 * open at once would stack on top of each other. `name` on `<details>` is the
 * platform's own answer — an accordion group with no script — and it also means a
 * reader never has to close a panel before opening the next.
 */
const EXPLAIN_GROUP = "worklist-explain";

export function Explain({ of }: { readonly of: ExplanationKey }) {
  const entry = EXPLANATIONS[of];
  return (
    <details className={styles.explain} name={EXPLAIN_GROUP}>
      <summary className={styles.explainToggle}>
        <span aria-hidden="true">ⓘ</span>
        <span className={styles.visuallyHidden}>{entry.title}</span>
      </summary>
      <div className={styles.explainBody}>
        <p className={styles.explainWhat}>{entry.what}</p>

        {entry.formula ? (
          <p className={styles.explainRow}>
            <span className={styles.explainTerm}>Computed as</span>
            <code className={styles.explainFormula}>{entry.formula}</code>
          </p>
        ) : null}

        {entry.inputs ? (
          <div className={styles.explainRow}>
            <span className={styles.explainTerm}>From</span>
            {/*
             * A list by role rather than by element. `<ul><li>` would be the
             * natural markup, but these panels render inside the row's own
             * `<li>`, and the e2e tier locates a row as `ol li` — nested list
             * items would make every input a match and turn a 24-row page into
             * a 360-match one. The ARIA roles give assistive technology the same
             * list semantics without adding `li` elements to that selector.
             */}
            <div className={styles.explainInputs} role="list">
              {entry.inputs.map((input) => (
                <span className={styles.explainInput} role="listitem" key={input}>
                  {input}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        <p className={styles.explainRow}>
          <span className={styles.explainTerm}>Source</span>
          <span>{entry.source}</span>
        </p>

        {entry.withheld ? (
          <p className={styles.explainRow}>
            <span className={styles.explainTerm}>Not shown</span>
            <span>{entry.withheld}</span>
          </p>
        ) : null}
      </div>
    </details>
  );
}
