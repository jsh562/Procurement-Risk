import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// OBJ1 VC6 / TR-001. The web boundary ships no behaviour in E001, so these
// assert the two properties the scaffold actually owes: a single lockfile, and
// a project root pinned rather than inferred. Root inference walks upward
// looking for a lockfile, and this repository has four sibling entries — a
// misdetected root produces broken module resolution and over-broad tracing.
const BOUNDARY = path.resolve(__dirname, "..");

describe("web boundary scaffold", () => {
  it("keeps the app directory at the boundary root, not nested under src", () => {
    expect(existsSync(path.join(BOUNDARY, "app"))).toBe(true);
    // Next.js accepts either app/ or src/app/ and one silently wins when both
    // exist; the ambiguity is the failure, so assert it cannot arise.
    expect(existsSync(path.join(BOUNDARY, "src", "app"))).toBe(false);
  });

  it("contains exactly one JavaScript lockfile", () => {
    const lockfiles = readdirSync(BOUNDARY).filter((name) =>
      ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"].includes(name),
    );
    expect(lockfiles).toEqual(["package-lock.json"]);
  });

  it("pins both the tracing root and the bundler root to this boundary", () => {
    const config = readFileSync(path.join(BOUNDARY, "next.config.ts"), "utf-8");
    expect(config).toMatch(/outputFileTracingRoot/);
    expect(config).toMatch(/turbopack:\s*\{[\s\S]*root:/);
  });
});
