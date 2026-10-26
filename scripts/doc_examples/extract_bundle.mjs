// Pull every endpoint object that holds a sampleResponse out of ScrapeCreators' docs bundle, without eval.
//
// Usage: node extract_bundle.mjs <bundle.js> <out.json>
// Parses the bundle with acorn, finds each ObjectExpression with a `sampleResponse` property, reads its
// identity (path, method, name, and the platform object it sits in) and converts the literal subtrees of
// sampleResponse, trimmedResponse and errorResponseExamples to JSON. Handles !0/!1, template literals
// without expressions, unary minus, exponent numbers, null, `void 0`, spreads, the minifier's
// `a.b`.split(`.`) string arrays, and references to other constants (`An.audienceRestricted`), resolved
// through the enclosing function scopes. Anything else is
// reported in `unresolved` with its source offset instead of being guessed.

import fs from "node:fs";
import * as acorn from "acorn";
import * as walk from "acorn-walk";

const [bundlePath, outPath] = process.argv.slice(2);
const src = fs.readFileSync(bundlePath, "utf8");
let ast;
try {
  ast = acorn.parse(src, { ecmaVersion: "latest", sourceType: "module" });
} catch {
  ast = acorn.parse(src, { ecmaVersion: "latest", sourceType: "script" });
}

const isScope = (n) => n.type === "Program" || /Function/.test(n.type);
const UNDEFINED = Symbol("undefined");

// name -> [{scope, init}] for every `var/let/const name = <init>` with an initializer.
const decls = new Map();
const hits = [];
walk.fullAncestor(ast, (node, _state, ancestors) => {
  if (node.type === "VariableDeclarator" && node.id.type === "Identifier" && node.init) {
    const scope = [...ancestors].reverse().find((a) => a !== node && isScope(a));
    if (!decls.has(node.id.name)) decls.set(node.id.name, []);
    decls.get(node.id.name).push({ scope, init: node.init });
  }
  if (node.type === "ObjectExpression" && node.properties.some((p) => keyOf(p) === "sampleResponse")) {
    hits.push({ node, ancestors: ancestors.slice(0, -1) });
  }
});

function keyOf(p) {
  if (p.type !== "Property" || p.computed) return null;
  if (p.key.type === "Identifier") return p.key.name;
  if (p.key.type === "Literal") return String(p.key.value);
  return null;
}

function prop(obj, name) {
  return obj.properties.find((p) => keyOf(p) === name)?.value;
}

function resolveIdent(name, ancestors, unresolved, at) {
  const found = decls.get(name) || [];
  for (const a of [...ancestors].reverse()) {
    if (!isScope(a)) continue;
    const inScope = found.filter((d) => d.scope === a);
    if (inScope.length === 1) return inScope[0];
    if (inScope.length > 1) {
      unresolved.push(`${at}: ${name} declared ${inScope.length}x in one scope`);
      return inScope[inScope.length - 1];
    }
  }
  unresolved.push(`${at}: no declaration for ${name}`);
  return null;
}

// The declaration's own ancestor chain is needed to resolve names inside it; recompute it lazily.
const chainCache = new Map();
function ancestorsOf(target) {
  if (chainCache.has(target)) return chainCache.get(target);
  let chain = null;
  walk.fullAncestor(ast, (node, _s, anc) => {
    if (!chain && node === target) chain = anc.slice(0, -1);
  });
  chainCache.set(target, chain || [ast]);
  return chain || [ast];
}

// Convert a literal subtree to a JS value; `ancestors` is the scope chain used to resolve identifiers.
function toValue(node, ancestors, unresolved, depth = 0) {
  const at = `@${node.start}`;
  if (depth > 60) {
    unresolved.push(`${at}: too deep`);
    return null;
  }
  switch (node.type) {
    case "ObjectExpression": {
      const out = {};
      for (const p of node.properties) {
        if (p.type === "SpreadElement") {
          const v = toValue(p.argument, ancestors, unresolved, depth + 1);
          if (v && typeof v === "object" && !Array.isArray(v)) Object.assign(out, v);
          else unresolved.push(`${at}: spread of non-object`);
          continue;
        }
        const k = keyOf(p);
        if (k === null || p.kind !== "init" || p.method) {
          unresolved.push(`@${p.start}: unsupported property`);
          continue;
        }
        const v = toValue(p.value, ancestors, unresolved, depth + 1);
        if (v !== UNDEFINED) out[k] = v;
      }
      return out;
    }
    case "ArrayExpression":
      return node.elements.map((e) => {
        if (e === null) return null;
        if (e.type === "SpreadElement") {
          unresolved.push(`@${e.start}: array spread`);
          return null;
        }
        const v = toValue(e, ancestors, unresolved, depth + 1);
        return v === UNDEFINED ? null : v; // JSON.stringify's rule for undefined in arrays
      });
    case "Literal":
      if (node.regex || typeof node.value === "bigint") {
        unresolved.push(`${at}: regex/bigint literal`);
        return null;
      }
      return node.value;
    case "TemplateLiteral":
      if (node.expressions.length) {
        unresolved.push(`${at}: template literal with expressions`);
        return node.quasis.map((q) => q.value.cooked).join("");
      }
      return node.quasis[0].value.cooked;
    case "UnaryExpression": {
      const a = node.argument;
      if (node.operator === "!" && a.type === "Literal" && typeof a.value === "number") return !a.value;
      if (node.operator === "-" && a.type === "Literal" && typeof a.value === "number") return -a.value;
      if (node.operator === "+" && a.type === "Literal" && typeof a.value === "number") return a.value;
      if (node.operator === "void") return UNDEFINED;
      unresolved.push(`${at}: unary ${node.operator}`);
      return null;
    }
    case "Identifier": {
      if (node.name === "undefined") return UNDEFINED;
      if (node.name === "Infinity" || node.name === "NaN") return null; // what JSON.stringify writes
      const d = resolveIdent(node.name, ancestors, unresolved, at);
      return d ? toValue(d.init, ancestorsOf(d.init), unresolved, depth + 1) : null;
    }
    case "MemberExpression": {
      if (node.computed || node.property.type !== "Identifier") {
        unresolved.push(`${at}: computed member`);
        return null;
      }
      const obj = toValue(node.object, ancestors, unresolved, depth + 1);
      if (obj && typeof obj === "object" && node.property.name in obj) return obj[node.property.name];
      unresolved.push(`${at}: member ${node.property.name} not found`);
      return null;
    }
    case "CallExpression": {
      // The minifier writes a string array as `a.b.c`.split(`.`); undo exactly that and nothing else.
      const c = node.callee;
      const str = (n) => (n.type === "Literal" && typeof n.value === "string" ? n.value
        : n.type === "TemplateLiteral" && !n.expressions.length ? n.quasis[0].value.cooked : null);
      if (c.type === "MemberExpression" && !c.computed && c.property.name === "split" && node.arguments.length === 1
          && str(c.object) !== null && str(node.arguments[0]) !== null) {
        return str(c.object).split(str(node.arguments[0]));
      }
      unresolved.push(`${at}: CallExpression`);
      return null;
    }
    default:
      unresolved.push(`${at}: ${node.type}`);
      return null;
  }
}

function literalString(obj, name) {
  const v = prop(obj, name);
  if (!v) return null;
  if (v.type === "Literal" && typeof v.value === "string") return v.value;
  if (v.type === "TemplateLiteral" && !v.expressions.length) return v.quasis[0].value.cooked;
  return null;
}

// The platform object ({id, name, endpoints: [...]}) an endpoint sits in, if any.
function platformOf(ancestors) {
  for (let i = ancestors.length - 1; i >= 1; i--) {
    const a = ancestors[i];
    if (a.type === "ObjectExpression" && prop(a, "endpoints")) {
      return { id: literalString(a, "id"), name: literalString(a, "name") };
    }
  }
  return null;
}

const out = [];
for (const { node, ancestors } of hits) {
  const scope = [...ancestors, node];
  const unresolved = [];
  const entry = {
    offset: node.start,
    platform: platformOf(ancestors),
    name: literalString(node, "name"),
    method: literalString(node, "method"),
    path: literalString(node, "path"),
  };
  for (const key of ["sampleResponse", "trimmedResponse", "errorResponseExamples"]) {
    const v = prop(node, key);
    if (v) entry[key] = toValue(v, scope, unresolved);
  }
  const params = prop(node, "params");
  if (params?.type === "ArrayExpression") {
    entry.params = params.elements.filter((e) => e?.type === "ObjectExpression").map((e) => ({
      name: literalString(e, "name"),
      type: literalString(e, "type"),
      placeholder: (() => {
        const p = prop(e, "placeholder");
        return p ? toValue(p, scope, []) : undefined;
      })(),
    }));
  }
  entry.unresolved = unresolved;
  out.push(entry);
}

fs.writeFileSync(outPath, JSON.stringify(out));
const count = (k) => out.filter((e) => k in e).length;
console.log(`endpoints ${out.length}; sampleResponse ${count("sampleResponse")}; trimmedResponse ${count("trimmedResponse")}; ` +
  `errorResponseExamples ${count("errorResponseExamples")}; with unresolved ${out.filter((e) => e.unresolved.length).length}`);
for (const e of out.filter((e) => e.unresolved.length)) console.log(e.method, e.path, e.unresolved.slice(0, 5));
