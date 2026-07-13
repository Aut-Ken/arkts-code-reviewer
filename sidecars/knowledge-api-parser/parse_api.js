#!/usr/bin/env node
"use strict";

const fs = require("fs");
const Parser = require("tree-sitter");
const ArkTS = require("tree-sitter-arkts");

const MIN_BUFFER_SIZE = 256 * 1024;
const OUTPUT_SCHEMA = "knowledge-api-parser-v1";
const PRODUCER_VERSION = "knowledge-api-parser-sidecar-v1.0.0";
const WRAPPER_TYPES = new Set(["ambient_declaration", "export_statement"]);
const CONTAINER_TYPES = new Set([
  "annotation_declaration",
  "class_declaration",
  "enum_declaration",
  "interface_declaration",
  "internal_module",
]);
const TOP_LEVEL_DECLARATIONS = new Map([
  ["annotation_declaration", "annotation"],
  ["class_declaration", "class"],
  ["enum_declaration", "enum"],
  ["function_signature", "function"],
  ["interface_declaration", "interface"],
  ["type_alias_declaration", "type"],
]);
const MEMBER_DECLARATIONS = new Map([
  ["method_signature", "method"],
  ["property_signature", "property"],
  ["public_field_definition", "property"],
]);
const MEMBER_PARENTS = new Set(["class_body", "interface_body"]);
const BODY_TYPES = new Set([
  "annotation_body",
  "class_body",
  "enum_body",
  "interface_body",
]);

function parseArgs(argv) {
  const options = { path: "<stdin>", module: "unknown" };
  for (let index = 2; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--path" && index + 1 < argv.length) {
      options.path = argv[index + 1];
      index += 1;
    } else if (arg === "--module" && index + 1 < argv.length) {
      options.module = argv[index + 1];
      index += 1;
    } else {
      throw new Error(`unsupported argument: ${arg}`);
    }
  }
  return options;
}

function nodeName(node) {
  const named = node.childForFieldName("name");
  if (named) {
    return named.text;
  }
  if (node.type === "enum_assignment") {
    return node.namedChild(0)?.text || null;
  }
  return null;
}

function declarationShell(node) {
  let shell = node;
  while (shell.parent && WRAPPER_TYPES.has(shell.parent.type)) {
    shell = shell.parent;
  }
  return shell;
}

function leadingJsdoc(node) {
  const previous = declarationShell(node).previousNamedSibling;
  if (previous?.type === "comment" && previous.text.trimStart().startsWith("/**")) {
    return previous.text;
  }
  return null;
}

function emptyMetadata() {
  return {
    deprecatedSince: null,
    permissions: [],
    sinceEntries: [],
    systemCapabilities: [],
  };
}

function parseMetadata(comment) {
  if (!comment) {
    return emptyMetadata();
  }
  const sinceEntries = [];
  const sincePattern = /@since\s+(\d+)(?:\.\d+\.\d+)?(?:\s+(dynamic(?:only)?|static(?:only)?))?/gi;
  for (const match of comment.matchAll(sincePattern)) {
    let mode = match[2]?.toLowerCase() || null;
    if (mode === "dynamiconly") {
      mode = "dynamic";
    } else if (mode === "staticonly") {
      mode = "static";
    }
    sinceEntries.push({ mode, version: Number.parseInt(match[1], 10) });
  }
  const deprecated = /@deprecated\s+since\s+(\d+)/i.exec(comment);
  const systemCapabilities = Array.from(
    comment.matchAll(/@syscap\s+([A-Za-z][A-Za-z0-9_.]+)/gi),
    (match) => match[1],
  );
  const permissions = [];
  for (const tag of comment.matchAll(/@permission\s+([^\r\n]*)/gi)) {
    for (const permission of tag[1].matchAll(
      /\b(?:ohos\.)?permission\.[A-Za-z0-9_.]+\b/gi,
    )) {
      permissions.push(permission[0]);
    }
  }
  return {
    deprecatedSince: deprecated ? Number.parseInt(deprecated[1], 10) : null,
    permissions: Array.from(new Set(permissions)).sort(),
    sinceEntries,
    systemCapabilities: Array.from(new Set(systemCapabilities)).sort(),
  };
}

function effectiveMetadata(node, inherited) {
  const own = parseMetadata(leadingJsdoc(node));
  return {
    deprecatedSince: own.deprecatedSince ?? inherited.deprecatedSince,
    permissions: own.permissions.length > 0 ? own.permissions : inherited.permissions,
    sinceEntries: own.sinceEntries.length > 0 ? own.sinceEntries : inherited.sinceEntries,
    systemCapabilities: own.systemCapabilities.length > 0
      ? own.systemCapabilities
      : inherited.systemCapabilities,
  };
}

function declarationSignature(node, kind) {
  if (new Set(["annotation", "class", "enum", "interface"]).has(kind)) {
    const body = node.namedChildren.find((child) => BODY_TYPES.has(child.type));
    if (body) {
      return node.text.slice(0, body.startIndex - node.startIndex).trim();
    }
  }
  return node.text.trim().replace(/;\s*$/, "");
}

function sinceProjection(metadata, canonicalName, node, diagnostics) {
  const unscoped = metadata.sinceEntries
    .filter((entry) => entry.mode === null)
    .map((entry) => entry.version);
  const scoped = metadata.sinceEntries.filter((entry) => entry.mode !== null);
  const availability = [];
  const symbolDiagnostics = [];
  if (scoped.length > 0) {
    for (const mode of ["dynamic", "static"]) {
      const versions = Array.from(
        new Set(
          scoped.filter((entry) => entry.mode === mode).map((entry) => entry.version),
        ),
      ).sort((left, right) => left - right);
      if (versions.length === 0) {
        continue;
      }
      if (versions.length > 1) {
        diagnostics.add(
          `conflicting_mode_since:${canonicalName}:L${node.startPosition.row + 1}:`
          + `${mode}=${versions.join("|")}`,
        );
        symbolDiagnostics.push("conflicting_mode_since");
      }
      availability.push({
        language_mode: mode,
        since: versions.length === 1 ? versions[0] : null,
        deprecated_since: null,
      });
    }
  }
  if (unscoped.length === 0) {
    return { availability, diagnostics: symbolDiagnostics.sort(), since: null };
  }
  const versions = Array.from(new Set(unscoped)).sort((left, right) => left - right);
  if (versions.length > 1) {
    diagnostics.add(
      `conflicting_unscoped_since:${canonicalName}:L${node.startPosition.row + 1}:`
      + versions.join(","),
    );
    symbolDiagnostics.push("conflicting_unscoped_since");
    return { availability, diagnostics: symbolDiagnostics.sort(), since: null };
  }
  return { availability, diagnostics: symbolDiagnostics.sort(), since: versions[0] };
}

function canonicalPart(name, kind) {
  return kind === "annotation" ? `@${name}` : name;
}

function symbolForNode(node, kind, owners, moduleName, inherited, diagnostics) {
  const name = nodeName(node);
  if (!name || (kind === "method" && name === "constructor")) {
    return null;
  }
  if (/^(?:private|protected)\b/.test(node.text.trimStart())) {
    return null;
  }
  const canonicalName = [...owners, canonicalPart(name, kind)].join(".");
  const metadata = effectiveMetadata(node, inherited);
  const projection = sinceProjection(metadata, canonicalName, node, diagnostics);
  return {
    availability: projection.availability,
    canonical_name: canonicalName,
    deprecated_since: metadata.deprecatedSince,
    diagnostics: projection.diagnostics,
    kind,
    module: moduleName,
    permissions: metadata.permissions,
    signature: declarationSignature(node, kind),
    since: projection.since,
    source_span: {
      start_line: node.startPosition.row + 1,
      end_line: node.endPosition.row + 1,
    },
    system_capabilities: metadata.systemCapabilities,
  };
}

function lexicalSymbols(node, owners, moduleName, inherited, diagnostics) {
  const declarationKind = node.child(0)?.text || "const";
  const symbols = [];
  for (const child of node.namedChildren) {
    if (child.type !== "variable_declarator") {
      continue;
    }
    const nameNode = child.childForFieldName("name");
    if (!nameNode) {
      continue;
    }
    const canonicalName = [...owners, nameNode.text].join(".");
    const metadata = effectiveMetadata(node, inherited);
    const projection = sinceProjection(metadata, canonicalName, node, diagnostics);
    symbols.push({
      availability: projection.availability,
      canonical_name: canonicalName,
      deprecated_since: metadata.deprecatedSince,
      diagnostics: projection.diagnostics,
      kind: "constant",
      module: moduleName,
      permissions: metadata.permissions,
      signature: `${declarationKind} ${child.text}`,
      since: projection.since,
      source_span: {
        start_line: node.startPosition.row + 1,
        end_line: node.endPosition.row + 1,
      },
      system_capabilities: metadata.systemCapabilities,
    });
  }
  return symbols;
}

function collectSymbols(root, moduleName) {
  const diagnostics = new Set();
  const symbols = [];
  let nodeCount = 0;
  let errorNodes = 0;
  let missingNodes = 0;

  function walk(node, owners, inherited) {
    nodeCount += 1;
    if (node.type === "ERROR") {
      errorNodes += 1;
    }
    if (node.isMissing) {
      missingNodes += 1;
    }

    let kind = TOP_LEVEL_DECLARATIONS.get(node.type) || null;
    if (MEMBER_DECLARATIONS.has(node.type) && MEMBER_PARENTS.has(node.parent?.type)) {
      kind = MEMBER_DECLARATIONS.get(node.type);
    }
    if (node.type === "enum_assignment" && node.parent?.type === "enum_body") {
      kind = "enum_member";
    }
    if (kind) {
      const symbol = symbolForNode(
        node,
        kind,
        owners,
        moduleName,
        inherited,
        diagnostics,
      );
      if (symbol) {
        symbols.push(symbol);
      }
    } else if (node.type === "lexical_declaration") {
      symbols.push(...lexicalSymbols(node, owners, moduleName, inherited, diagnostics));
    }

    let childOwners = owners;
    let childMetadata = inherited;
    if (CONTAINER_TYPES.has(node.type)) {
      const name = nodeName(node);
      if (name) {
        const ownerKind = node.type === "annotation_declaration" ? "annotation" : "owner";
        childOwners = [...owners, canonicalPart(name, ownerKind)];
      }
      childMetadata = effectiveMetadata(node, inherited);
    }
    for (const child of node.children) {
      walk(child, childOwners, childMetadata);
    }
  }

  walk(root, [], emptyMetadata());
  symbols.sort((left, right) => {
    if (left.canonical_name !== right.canonical_name) {
      return left.canonical_name < right.canonical_name ? -1 : 1;
    }
    if (left.signature !== right.signature) {
      return left.signature < right.signature ? -1 : 1;
    }
    if (left.source_span.start_line !== right.source_span.start_line) {
      return left.source_span.start_line - right.source_span.start_line;
    }
    return left.source_span.end_line - right.source_span.end_line;
  });
  return {
    diagnostics: Array.from(diagnostics).sort(),
    errorNodes,
    missingNodes,
    nodeCount,
    symbols,
  };
}

function main() {
  const options = parseArgs(process.argv);
  const source = fs.readFileSync(0, "utf8");
  const parser = new Parser();
  parser.setLanguage(ArkTS);
  const bufferSize = Math.max(MIN_BUFFER_SIZE, Buffer.byteLength(source, "utf8") + 1024);
  const tree = parser.parse(source, null, { bufferSize });
  const result = collectSymbols(tree.rootNode, options.module);
  process.stdout.write(`${JSON.stringify({
    output_schema: OUTPUT_SCHEMA,
    producer_version: PRODUCER_VERSION,
    parser: "tree-sitter-arkts",
    parser_version: require("tree-sitter-arkts/package.json").version,
    path: options.path,
    root_type: tree.rootNode.type,
    node_count: result.nodeCount,
    error_nodes: result.errorNodes,
    missing_nodes: result.missingNodes,
    symbols: result.symbols,
    diagnostics: result.diagnostics,
  })}\n`);
}

try {
  main();
} catch (error) {
  process.stdout.write(`${JSON.stringify({
    error: error && error.message ? error.message : String(error),
  })}\n`);
  process.exitCode = 1;
}
