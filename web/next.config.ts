import type { NextConfig } from "next";

// `output: "standalone"` emits a self-contained server bundle, which is what
// web/Dockerfile's runtime stage copies so it needs no node_modules. Vercel
// must not get it: its builder runs its own file tracing and reads the
// .nft.json trace files `next build` normally leaves in .next, and standalone
// mode relocates them - the build fails on a missing next-server.js.nft.json.
// Vercel sets VERCEL=1 during builds, so the container build keeps standalone
// and Vercel gets the default output it expects.
const nextConfig: NextConfig = {
  ...(process.env.VERCEL ? {} : { output: "standalone" as const }),
};

export default nextConfig;
