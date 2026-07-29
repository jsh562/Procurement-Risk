import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * The presentation contract, and the accessibility obligations, on a laid-out
 * page.
 *
 * FR-032 makes these assertions rather than review comments, and FR-040 makes
 * an assertion that does not run in the merge gate evidence nothing. They are
 * here rather than in Vitest because each is a property of layout or of the
 * accessibility tree, and jsdom supplies neither: it computes no styles, so a
 * type-scale comparison against it would compare two empty strings and pass.
 *
 * The page under test is served by the real boundary against the real schema —
 * a stubbed response would let these specs assert a layout for data no server
 * produces.
 */

const worklist = async (page: Page) => {
  await page.goto("/worklist");
  await expect(page.getByRole("heading", { name: "Delivery risk worklist" })).toBeVisible();
};

/** The computed font size of an element, in pixels. */
const fontSize = (locator: Locator) =>
  locator.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));

/** The computed font weight, normalised — `normal` and `bold` are keywords. */
const fontWeight = (locator: Locator) =>
  locator.evaluate((el) => {
    const weight = getComputedStyle(el).fontWeight;
    return weight === "normal" ? 400 : weight === "bold" ? 700 : parseInt(weight, 10);
  });

test.describe("the presentation contract (FR-032)", () => {
  test("the secondary region is smaller than the primary and never heavier", async ({ page }) => {
    // FR-032 states the *direction* because "distinct" without one is satisfied
    // by a secondary region set larger than the primary — which inverts the
    // very subordination FR-009 asks for.
    await worklist(page);
    const row = page.locator("ol li").first();

    // Both halves are measured over *descendants*, not over the container.
    // FR-032 asks for two properties — "a **smaller** type scale than the
    // primary region's and never a heavier weight" — and an assertion on the
    // container element says nothing about what is rendered inside it. The
    // weight half was corrected first; this is the size half, which had the
    // identical blind spot and was left standing.
    const primary = await fontSize(row.locator("[class*='identity']"));
    const secondarySizes = await row
      .locator("[class*='secondary']")
      .evaluate((el) =>
        [el, ...el.querySelectorAll("*")].map((node) =>
          parseFloat(getComputedStyle(node as Element).fontSize),
        ),
      );

    expect(secondarySizes.length).toBeGreaterThan(1);
    expect(Math.max(...secondarySizes)).toBeLessThan(primary);

    // Measured over every descendant of the secondary region, not over the
    // container. The container's own weight says nothing about what is rendered
    // inside it — a mark set heavier than the primary would sit in a child and
    // leave a container-only assertion green, which is what the earlier
    // revision of this test did while the stale mark was added at weight 600.
    const primaryWeight = await fontWeight(row.locator("[class*='identity']"));
    const secondaryWeights = await row.locator("[class*='secondary']").evaluate((el) =>
      [el, ...el.querySelectorAll("*")].map((node) => {
        const weight = getComputedStyle(node as Element).fontWeight;
        return weight === "normal" ? 400 : weight === "bold" ? 700 : parseInt(weight, 10);
      }),
    );

    expect(secondaryWeights.length).toBeGreaterThan(1);
    expect(Math.max(...secondaryWeights)).toBeLessThanOrEqual(primaryWeight);
  });

  test("both directions of the probability carry equal prominence", async ({ page }) => {
    // FR-006. Same type scale, same weight, adjacent within one statement. A
    // pair with one direction set larger is a single framing with a footnote.
    await worklist(page);
    // The probability's two directions live on a row that has one.
    const figures = page
      .locator("ol li[data-state='nominal']")
      .first()
      .locator("[class*='missProbability'] [class*='figure']");

    const first = figures.nth(0);
    const second = figures.nth(1);
    expect(await fontSize(first)).toBe(await fontSize(second));
    expect(await fontWeight(first)).toBe(await fontWeight(second));
  });

  test("the as-of date is readable without hover or expansion", async ({ page }) => {
    // FR-019. A figure whose frame is one interaction away is a figure most
    // readers meet unanchored.
    await worklist(page);
    const secondary = page.locator("ol li").first().locator("[class*='secondary']");

    await expect(secondary.getByText(/Forecast as of/)).toBeVisible();
    // Visible without any pointer or keyboard interaction having occurred: the
    // assertion above ran before this test touched the page.
    expect(await secondary.getByText(/Forecast as of/).count()).toBeGreaterThan(0);
  });

  test("the reading order in the document is identity, need-by, probability, pair", async ({
    page,
  }) => {
    // FR-032. The same order in the rendered document and in the accessibility
    // tree's traversal, so a coordinator hearing the row and one seeing it meet
    // it in the same sequence.
    await worklist(page);
    // A *nominal* row, not merely the first one. The top of this fixture's
    // ranking is an already-late line, which withholds the miss probability
    // under FR-030 — so asserting the four-quantity order against it would be
    // asserting the order of three quantities and one absence.
    const text = (await page.locator("ol li[data-state='nominal']").first().innerText()).replace(
      /\s+/g,
      " ",
    );

    const identity = text.search(/PRJ-\d{3} · PO-/);
    const needBy = text.indexOf("Need by");
    const probability = text.search(/miss the date/);
    const pair = text.indexOf("Likely delivery window");

    expect(identity).toBeGreaterThanOrEqual(0);
    expect(identity).toBeLessThan(needBy);
    expect(needBy).toBeLessThan(probability);
    expect(probability).toBeLessThan(pair);
  });

  test("no single predicted delivery date appears anywhere on the page", async ({ page }) => {
    // FR-007, FR-041. The enumeration binds every surface, and the rendered
    // page is the one a coordinator actually reads.
    await worklist(page);
    const text = await page.locator("body").innerText();

    expect(text).not.toMatch(/expected (delivery|arrival)/i);
    expect(text).not.toMatch(/\bETA\b/);
    expect(text.toLowerCase()).not.toContain("expected harm");
    expect(text).not.toMatch(/will arrive|arrives on/i);
  });

  test("the offered sort keys are exactly the four, on screen", async ({ page }) => {
    // FR-026, FR-032. Asserted against the rendered control, because that is
    // what a coordinator can actually choose from.
    await worklist(page);
    const options = await page.getByLabel("Order by").locator("option").allTextContents();

    expect(options).toHaveLength(4);
    const joined = options.join(" ").toLowerCase();
    for (const forbidden of ["p50", "p80", "median", "delivery date", "eightieth"]) {
      expect(joined).not.toContain(forbidden);
    }
  });

  test("the active sort key, direction and tiebreak are stated", async ({ page }) => {
    // FR-047. Under the default key the tiebreak orders the whole zero-harm
    // block, so an order the coordinator cannot account for reads as arbitrary.
    await worklist(page);
    const heading = page.getByRole("heading", { name: /^Ranked by/ });

    await expect(heading).toContainText("expected schedule harm");
    await expect(heading).toContainText("highest first");
    await expect(page.getByText(/Ties are broken by/)).toBeVisible();
  });
});

test.describe("accessibility (FR-048, FR-049, FR-050, FR-051)", () => {
  test("each ranked row states its position as text", async ({ page }) => {
    // FR-048. With the harm score absent under FR-041, position read off the
    // screen's geometry would leave the product's entire output conveyed by
    // nothing at all to a screen reader.
    await worklist(page);
    const first = page.locator("ol li").first();

    await expect(first).toContainText("Position");
    await expect(first.locator("[class*='rank']")).toContainText("1");
  });

  test("the ranked group announces that it is ordered and how many it holds", async ({ page }) => {
    // FR-048. Position is never carried by vertical placement alone.
    await worklist(page);

    await expect(page.locator("ol")).toBeVisible();
    await expect(page.getByRole("heading", { name: /^Ranked by/ })).toContainText(/\d+ lines/);
  });

  test("the quantile pair is reachable under one accessible name", async ({ page }) => {
    // FR-049. A linearised reading of two bare numbers is exactly the
    // independent-quantile reading FR-004 removes, and it reissues the
    // invitation to treat one of them alone as the answer.
    await worklist(page);
    const pair = page
      .locator("ol li")
      .first()
      .getByRole("region", {
        name: /Likely delivery window/,
      });

    await expect(pair).toBeVisible();
    await expect(pair).toContainText(/\d+ days/);

    // FR-005 and SC-013 on the rendered page rather than only in the unit tier.
    // The population has to sit against the denominator: "20 in 100 land later"
    // passed the weaker `toContainText("land later")` while naming no reference
    // class at all, and was read in the field as a count of parts.
    await expect(pair).toContainText(/in 100 comparable orders land later/);
  });

  test("a degraded state is carried by text, not only by colour", async ({ page }) => {
    // FR-050. A state carried only by colour is invisible to a coordinator who
    // cannot see it and to every automated check of SC-009's wording rule.
    await worklist(page);
    const degraded = page.locator("li[data-state='already_late']").first();

    await expect(degraded).toBeVisible();
    await expect(degraded).toContainText("Need-by date precedes the forecast anchor");
  });

  test("the whole adjustment flow is operable from the keyboard", async ({ page }) => {
    // FR-051. Keyboard operation is not a fallback: it is how the surface works
    // for anyone not using a pointer, and a control reachable only by click is
    // one they cannot use at all.
    await worklist(page);
    // Addressed by its own id, not by position. The list reorders under the
    // adjustment, so `.first()` afterwards is whatever row moved to the top —
    // and the requirement is that focus stays on *the control the coordinator
    // was using*, which is identified by its line and not by where it sits.
    const controlId = await page
      .locator("input[type='date']")
      .first()
      .evaluate((el) => el.id);
    const control = page.locator(`input[id="${controlId}"]`);

    await control.focus();
    await expect(control).toBeFocused();

    await control.fill("2026-07-01");
    await control.press("Enter");

    // FR-046. Focus stays on the control rather than returning to the top of a
    // list that has just reordered underneath it.
    await expect(page.getByRole("status", { name: "Adjustment status" })).toContainText(
      /^Adjustment applied\./,
      { timeout: 10_000 },
    );
    await expect(control).toBeFocused();
  });

  test("both acknowledgement outcomes reach the same live region", async ({ page }) => {
    // FR-012, FR-046. Two regions would let one be configured away and leave
    // half the outcomes silent.
    await worklist(page);
    const region = page.getByRole("status", { name: "Adjustment status" });
    await expect(region).toHaveAttribute("aria-live", "polite");

    await page.locator("input[type='date']").first().fill("2026-07-01");
    await expect(region).toContainText(/^Adjustment applied./, { timeout: 10_000 });
    await expect(region).toContainText(/order (changed|is unchanged)/i);
  });

  test("the acknowledgement does not expire on a timer", async ({ page }) => {
    // FR-012. Persistent rather than timed, because a message that disappears
    // is the documented way an acknowledgement is missed.
    await worklist(page);
    const region = page.getByRole("status", { name: "Adjustment status" });

    await page.locator("input[type='date']").first().fill("2026-07-01");
    await expect(region).toContainText(/^Adjustment applied\./, { timeout: 10_000 });

    await page.waitForTimeout(6_000);
    await expect(region).toContainText(/^Adjustment applied\./);
  });

  test("a bounded probability is spoken as a bound rather than as a numeral", async ({ page }) => {
    // FR-051: "FR-008's bounded forms MUST be announced as words, 'less than
    // one percent' and 'greater than ninety-nine percent': the < and > glyphs
    // are read inconsistently or dropped outright, and a dropped < turns <1%
    // into a flat 1%."
    //
    // Unguarded, deliberately. The previous revision wrapped this in
    // `if (count > 0)`, so it would have vacated silently the day the fixture
    // stopped producing a bound — an assertion that cannot fail is not one.
    // PO-4476-2's residual tail is 0.075%, so a bound is guaranteed present; if
    // it ever is not, this test should fail rather than quietly pass.
    await worklist(page);

    const bounded = page.getByText("<1%", { exact: true }).first();
    await expect(bounded).toBeVisible();

    // The glyph is hidden from assistive technology and the words carry the
    // meaning, so a screen reader hears the bound once rather than twice.
    await expect(bounded).toHaveAttribute("aria-hidden", "true");

    const row = bounded.locator("xpath=ancestor::li[1]");
    await expect(row).toContainText("less than one percent");
    await expect(row).not.toContainText(/\b0%/);
    await expect(row).not.toContainText(/\b100%/);
  });

  test("a stale run marks every row, not only the page banner", async ({ page }) => {
    // FR-029. A page banner stops carrying once rows are sorted, filtered, or
    // read one at a time. The fixture's run is anchored 2026-06-01, so on any
    // realistic clock it is far past the seven-day threshold.
    await worklist(page);

    await expect(page.locator("section[data-state='stale_run']")).toBeVisible();
    await expect(page.locator("ol li").first()).toContainText("out of date");
  });
});

test.describe("the named observables (FR-034)", () => {
  test("SC-001: the first ranked row is the maximum-harm line, at rank 1, unexpanded", async ({
    page,
  }) => {
    // FR-034 names the observable rather than leaving the capability to
    // judgment: the recorded risk is that no user is available to validate this
    // surface, so a capability nobody can observe is checked by nobody.
    await worklist(page);
    const first = page.locator("ol li").first();

    await expect(first.locator("[class*='rank']")).toContainText("1");
    await expect(first).toBeVisible();

    // "Reachable without expanding or opening anything", asserted as the
    // property SC-001 states rather than as the absence of a mechanism.
    //
    // This previously read `locator("details, [aria-expanded='false']").count()
    // === 0` — no disclosure element anywhere on the page. That was a sound
    // proxy while the page had no disclosures at all, but it cannot tell a
    // control that *hides a figure* from one that only adds an explanation
    // beside it. The provenance panels are the second kind: every comparison
    // quantity renders whether they are open or shut.
    //
    // So the check is now the stronger one. Every disclosure starts closed, and
    // with all of them closed the row's figures are on screen. A disclosure that
    // ever gated a figure would fail this, where counting elements would have
    // passed a page whose figures were all hidden behind an *open* one.
    for (const disclosure of await page.locator("details").all()) {
      await expect(disclosure).not.toHaveAttribute("open", /.*/);
    }
    // `.first()` on the need-by locator: the substring also matches the
    // adjustment control that sits beside the date. The figure is the first.
    await expect(first.locator("[class*='identity']").first()).toBeVisible();
    await expect(first.locator("[class*='needBy']").first()).toBeVisible();
    await expect(first.getByRole("region", { name: /Likely delivery window/ })).toBeVisible();
  });

  test("SC-004: the three score inputs sit in one row element", async ({ page }) => {
    // FR-009's decomposition is the whole set — the quantile pair in the
    // primary region, and the criticality and calendar margin in the
    // subordinate one. Naming two of the three would ask a coordinator to
    // account for a position from a partial decomposition.
    await worklist(page);
    const first = page.locator("ol li").first();

    await expect(first).toContainText("Likely delivery window");
    await expect(first).toContainText(/Criticality \d of 5/);
    await expect(first).toContainText(/days (of margin|past the anchor)/);
  });

  test("SC-008: an excluded line is absent from the ranking under every sort key", async ({
    page,
  }) => {
    // FR-016, FR-045. The exclusion survives every ordering, which is what
    // makes the group an inventory rather than a discard pile.
    await worklist(page);
    const excluded = page.locator("ul li[data-state='not_covered']").first();
    await expect(excluded).toBeVisible();
    const identity = await excluded.locator("[class*='identity']").innerText();

    for (const key of ["expected_harm", "need_by_date", "criticality", "calendar_margin"]) {
      await page.getByLabel("Order by").selectOption(key);
      await expect(page.locator("ol")).toBeVisible();
      await expect(page.locator("ol")).not.toContainText(identity);
      await expect(page.locator("ul li[data-state='not_covered']").first()).toContainText(identity);
    }
  });
});
