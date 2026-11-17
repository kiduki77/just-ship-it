// node run.mjs MODULE EXPORT ARG...: call one export of a console module on each argument and print the results as a
// JSON list. Run it from web/, so the module's own imports resolve against web/node_modules.
import { register } from "node:module";
import { pathToFileURL } from "node:url";

globalThis.document = { documentElement: {} };
register(new URL("./hooks.mjs", import.meta.url), pathToFileURL(process.cwd() + "/"));
const [modulePath, name, ...args] = process.argv.slice(2);
const mod = await import(pathToFileURL(modulePath).href);
console.log(JSON.stringify(args.map((a) => mod[name](a))));
