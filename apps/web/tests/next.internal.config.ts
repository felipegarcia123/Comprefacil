import type { NextConfig } from "next";

// Mounted over /app/next.config.ts only in the isolated COMP-S2-004 test.
const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  allowedDevOrigins: ["comps2004-web"],
};

export default nextConfig;
