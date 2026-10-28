import type { NextConfig } from "next";

// Static export: the FastAPI backend serves frontend/out in production (one Cloud Run service).
// In development, run `npm run dev` with the backend on :8000 and NEXT_PUBLIC_API_BASE=http://localhost:8000.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
