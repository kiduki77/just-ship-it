// Node module hooks that load the console's TypeScript as the bundler would: extensionless relative imports, and
// types stripped by rolldown's transform (web/node_modules).
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const fromWeb = createRequire(process.cwd() + "/package.json");
const { transformSync } = await import(pathToFileURL(fromWeb.resolve("rolldown/experimental")).href);

export async function resolve(spec, ctx, next) {
  if (spec.startsWith(".") && !/\.[cm]?[jt]sx?$/.test(spec)) {
    for (const ext of [".ts", ".tsx", "/index.ts"]) {
      try {
        return await next(spec + ext, ctx);
      } catch {}
    }
  }
  return next(spec, ctx);
}

export async function load(url, ctx, next) {
  if (!/\.tsx?$/.test(url)) return next(url, ctx);
  const file = fileURLToPath(url);
  const out = transformSync(file, await readFile(file, "utf8"));
  if (out.errors?.length) throw new Error(JSON.stringify(out.errors));
  return { format: "module", source: out.code, shortCircuit: true };
}
