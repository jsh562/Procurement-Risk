import path from "node:path";
import type { NextConfig } from "next";

// Both roots are pinned to this directory rather than left to inference.
// The repository holds four sibling entries under /src, three of them Python,
// and root inference walks upward looking for a lockfile — a misdetected root
// has historically produced broken module resolution and over-broad tracing.
const boundaryRoot = path.resolve(__dirname);

const nextConfig: NextConfig = {
  outputFileTracingRoot: boundaryRoot,
  turbopack: {
    root: boundaryRoot,
  },
};

export default nextConfig;
