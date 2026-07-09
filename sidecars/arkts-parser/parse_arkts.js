#!/usr/bin/env node
"use strict";

const fs = require("fs");
const Parser = require("tree-sitter");
const ArkTS = require("tree-sitter-arkts");

const MIN_BUFFER_SIZE = 256 * 1024;
const ARKUI_MODIFIERS = new Set([
  "accessibilityDescription",
  "accessibilityGroup",
  "accessibilityLevel",
  "accessibilityText",
  "align",
  "alignItems",
  "animation",
  "aspectRatio",
  "backgroundColor",
  "border",
  "borderRadius",
  "fontColor",
  "fontSize",
  "fontWeight",
  "height",
  "layoutWeight",
  "margin",
  "objectFit",
  "onAppear",
  "onBlur",
  "onChange",
  "onClick",
  "onComplete",
  "onDisAppear",
  "onError",
  "onFocus",
  "onTouch",
  "padding",
  "placeholder",
  "position",
  "rotate",
  "transition",
  "visibility",
  "width",
]);

function parseArgs(argv) {
  const options = { path: "<stdin>" };
  for (let index = 2; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--path" && index + 1 < argv.length) {
      options.path = argv[index + 1];
      index += 1;
    }
  }
  return options;
}

function span(node) {
  return {
    start_line: node.startPosition.row + 1,
    end_line: node.endPosition.row + 1,
    start_col: node.startPosition.column + 1,
    end_col: node.endPosition.column + 1,
  };
}

function childText(node, fieldName) {
  const child = node.childForFieldName(fieldName);
  return child ? child.text : null;
}

function decoratorName(text) {
  const match = /^@([A-Za-z_$][A-Za-z0-9_$]*)/.exec(text.trim());
  return match ? `@${match[1]}` : text.trim();
}

function hasDecorator(node, decorator) {
  for (let index = 0; index < node.childCount; index += 1) {
    const child = node.child(index);
    if (child.type === "decorator" && decoratorName(child.text) === decorator) {
      return true;
    }
  }
  return false;
}

function addDeclaration(result, node, kind, name, stack) {
  if (!name) {
    return null;
  }
  const parent = stack.length > 0 ? stack[stack.length - 1] : null;
  let qualifiedName = name;
  if (parent && parent.qualified_name) {
    qualifiedName = `${parent.qualified_name}.${name}`;
  }
  const declaration = {
    kind,
    name,
    qualified_name: qualifiedName,
    parent_name: parent ? parent.qualified_name : null,
    span: span(node),
  };
  result.declarations.push(declaration);
  result.symbols.add(name);
  result.symbols.add(qualifiedName);
  return declaration;
}

function declarationForNode(result, node, stack) {
  if (node.type === "struct_declaration") {
    return addDeclaration(result, node, "struct", childText(node, "name"), stack);
  }
  if (node.type === "class_declaration" || node.type === "abstract_class_declaration") {
    return addDeclaration(result, node, "class", childText(node, "name"), stack);
  }
  if (node.type === "function_declaration" || node.type === "generator_function_declaration") {
    const name = childText(node, "name");
    const kind = hasDecorator(node, "@Builder") ? "builder" : "function";
    return addDeclaration(result, node, kind, name, stack);
  }
  if (node.type === "method_definition") {
    const name = childText(node, "name");
    const kind = name === "build" ? "build_method" : "method";
    if (/^\s*async\b/.test(node.text)) {
      result.syntax.add("async_fn");
    }
    return addDeclaration(result, node, kind, name, stack);
  }
  if (node.type === "arkui_component_expression") {
    const name = childText(node, "function");
    if (name) {
      result.components.add(name);
      return addDeclaration(result, node, "ui_block", name, stack);
    }
  }
  return null;
}

function collectArkuiAttributes(result, node) {
  for (let index = 0; index < node.childCount; index += 1) {
    const child = node.child(index);
    const field = node.fieldNameForChild(index);
    if (field === "property" && child.type === "property_identifier") {
      result.attributes.add(child.text);
    }
  }
}

function normalizeCallText(text) {
  let normalized = "";
  let quote = null;
  let escaped = false;
  for (const char of text) {
    if (quote) {
      normalized += char;
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === quote) {
        quote = null;
      }
      continue;
    }
    if (char === "'" || char === '"' || char === "`") {
      quote = char;
      normalized += char;
      continue;
    }
    if (!/\s/.test(char)) {
      normalized += char;
    }
  }
  return normalized;
}

function collectCallAttribute(result, functionText) {
  const normalized = normalizeCallText(functionText);
  const match = /\.([A-Za-z_$][A-Za-z0-9_$]*)$/.exec(normalized);
  if (!match) {
    return;
  }
  const modifier = match[1];
  if (ARKUI_MODIFIERS.has(modifier) || /^on[A-Z]/.test(modifier)) {
    result.attributes.add(modifier);
  }
}

function walk(node, result, stack) {
  result.node_count += 1;
  if (node.type === "ERROR") {
    result.error_nodes += 1;
  }
  if (node.isMissing) {
    result.missing_nodes += 1;
  }

  if (node.type === "decorator") {
    result.decorators.add(decoratorName(node.text));
  } else if (node.type === "call_expression") {
    const fn = childText(node, "function");
    if (fn) {
      result.calls.add(normalizeCallText(fn));
      collectCallAttribute(result, fn);
    }
  } else if (node.type === "await_expression") {
    result.syntax.add("await_expr");
  } else if (node.type === "arrow_function") {
    result.syntax.add("arrow_fn");
  } else if (node.type === "try_statement" || node.type === "catch_clause") {
    result.syntax.add("try_catch");
  } else if (node.text === "Promise") {
    result.syntax.add("promise");
  }

  if (node.type === "arkui_component_expression") {
    collectArkuiAttributes(result, node);
  }

  const declaration = declarationForNode(result, node, stack);
  const nextStack = declaration ? stack.concat([declaration]) : stack;
  for (let index = 0; index < node.childCount; index += 1) {
    walk(node.child(index), result, nextStack);
  }
}

function toOutput(result, path, rootType) {
  return {
    parser: "tree-sitter-arkts",
    parser_version: require("tree-sitter-arkts/package.json").version,
    path,
    root_type: rootType,
    node_count: result.node_count,
    error_nodes: result.error_nodes,
    missing_nodes: result.missing_nodes,
    components: Array.from(result.components).sort(),
    calls: Array.from(result.calls).sort(),
    decorators: Array.from(result.decorators).sort(),
    attributes: Array.from(result.attributes).sort(),
    symbols: Array.from(result.symbols).sort(),
    syntax: Array.from(result.syntax).sort(),
    declarations: result.declarations,
  };
}

function main() {
  const options = parseArgs(process.argv);
  const source = fs.readFileSync(0, "utf8");
  const parser = new Parser();
  parser.setLanguage(ArkTS);
  const bufferSize = Math.max(MIN_BUFFER_SIZE, Buffer.byteLength(source, "utf8") + 1024);
  const tree = parser.parse(source, null, { bufferSize });

  const result = {
    node_count: 0,
    error_nodes: 0,
    missing_nodes: 0,
    components: new Set(),
    calls: new Set(),
    decorators: new Set(),
    attributes: new Set(),
    symbols: new Set(),
    syntax: new Set(),
    declarations: [],
  };

  walk(tree.rootNode, result, []);
  process.stdout.write(`${JSON.stringify(toOutput(result, options.path, tree.rootNode.type))}\n`);
}

try {
  main();
} catch (error) {
  process.stdout.write(JSON.stringify({
    error: error && error.message ? error.message : String(error),
  }) + "\n");
  process.exitCode = 1;
}
