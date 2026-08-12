import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits a self-contained server bundle so the runtime Docker stage does
  // not need node_modules.
  output: "standalone",
};

export default nextConfig;
