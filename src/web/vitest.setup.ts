/**
 * Test setup for the web boundary.
 *
 * `jest-dom` supplies matchers that assert over the *rendered* DOM rather than
 * over strings — `toHaveTextContent`, `toBeEmptyDOMElement`, `toHaveAttribute`.
 * That matters for the obligations this feature carries: FR-050's "carried by
 * text present in the accessibility tree" is a statement about the tree, and a
 * substring check over serialised markup would pass on text that is present but
 * hidden from it.
 */
import "@testing-library/jest-dom/vitest";
