// Generate TypeScript types from the FastAPI OpenAPI schema.
// Usage: backend running at http://localhost:8000, then `npm run generate:api`.
// Output is src/lib/api-types.ts. Do not edit the generated file by hand.
import { writeFileSync } from "node:fs";
import openapiTS from "openapi-typescript";

const url = process.env.OPENAPI_URL ?? "http://localhost:8000/openapi.json";
const out = new URL("../src/lib/api-types.ts", import.meta.url);
const types = await openapiTS(new URL(url), { exportType: true });
writeFileSync(out, `/**\n * Generated from ${url}. Do not edit by hand.\n * Regenerate: npm run generate:api\n */\n${types}`);
console.log(`wrote ${out.pathname}`);
