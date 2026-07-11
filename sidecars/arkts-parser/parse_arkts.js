#!/usr/bin/env node
"use strict";

const fs = require("fs");
const Parser = require("tree-sitter");
const ArkTS = require("tree-sitter-arkts");

const MIN_BUFFER_SIZE = 256 * 1024;
const NON_COMPONENT_CALL_ROOTS = new Set([
  "Array",
  "Boolean",
  "Date",
  "Map",
  "Number",
  "Object",
  "Promise",
  "RegExp",
  "Set",
  "String",
]);
const PAGE_TRANSITION_COMPONENTS = new Set(["PageTransitionEnter", "PageTransitionExit"]);
const UI_STRUCT_DECORATORS = ("@Component @ComponentV2 @CustomDialog").split(" ");

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

function firstNamedChild(node) {
  return node && node.namedChildCount > 0 ? node.namedChild(0) : null;
}

function nextNonCommentSibling(node) {
  let sibling = node ? node.nextNamedSibling : null;
  while (sibling && sibling.type === "comment") {
    sibling = sibling.nextNamedSibling;
  }
  return sibling;
}

function positionsTouch(left, right) {
  return (
    left.endPosition.row === right.startPosition.row
    && left.endPosition.column === right.startPosition.column
  );
}

function isArgumentsContinuation(statement) {
  return (
    statement?.type === "expression_statement"
    && statement.text.trimStart().startsWith("(")
  );
}

function leadingDotModifierName(statement) {
  const match = /^\.([A-Za-z_$][A-Za-z0-9_$]*)$/.exec(
    firstNamedChild(statement)?.text || "",
  );
  return match ? match[1] : null;
}

function componentContinuationInfo(statement) {
  const attributes = new Set();
  let endNode = statement;
  while (endNode?.type === "expression_statement") {
    const next = nextNonCommentSibling(endNode);
    const modifierName = leadingDotModifierName(next);
    if (modifierName) {
      const argumentsStatement = nextNonCommentSibling(next);
      if (
        !isArgumentsContinuation(argumentsStatement)
        || !positionsTouch(next, argumentsStatement)
      ) {
        break;
      }
      attributes.add(modifierName);
      for (const attribute of expressionSpineAttributes(firstNamedChild(argumentsStatement))) {
        attributes.add(attribute);
      }
      endNode = argumentsStatement;
      continue;
    }

    if (
      isArgumentsContinuation(next)
      && positionsTouch(endNode, next)
      && /\.[A-Za-z_$][A-Za-z0-9_$]*$/.test(endNode.text.trimEnd())
    ) {
      for (const attribute of expressionSpineAttributes(firstNamedChild(endNode))) {
        attributes.add(attribute);
      }
      for (const attribute of expressionSpineAttributes(firstNamedChild(next))) {
        attributes.add(attribute);
      }
      endNode = next;
      continue;
    }
    break;
  }
  return { attributes: Array.from(attributes), endNode };
}

function arkuiComponentSpan(node) {
  const result = span(node);
  const { endNode } = componentContinuationInfo(node.parent);
  if (endNode && endNode !== node.parent) {
    result.end_line = endNode.endPosition.row + 1;
    result.end_col = endNode.endPosition.column + 1;
  }
  return result;
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

  // tree-sitter-arkts represents decorators on class/struct methods as
  // preceding siblings in the class body rather than children of the method.
  let sibling = node.previousNamedSibling;
  while (sibling && (sibling.type === "decorator" || sibling.type === "comment")) {
    if (sibling.type === "decorator" && decoratorName(sibling.text) === decorator) {
      return true;
    }
    sibling = sibling.previousNamedSibling;
  }
  return false;
}

function isComponentName(name) {
  return (
    /^[A-Z][A-Za-z0-9_$]*$/.test(name || "")
    && !NON_COMPONENT_CALL_ROOTS.has(name)
  );
}

function isCustomDialogBuilderValue(node) {
  return node.parent?.type === "pair" && childText(node.parent, "key") === "builder";
}

function callChainInfo(node) {
  if (node.type !== "call_expression") {
    return null;
  }

  const attributes = [];
  let current = node;
  while (current?.type === "call_expression") {
    const fn = current.childForFieldName("function");
    if (!fn) {
      return null;
    }
    if (fn.type === "identifier") {
      return { name: fn.text, attributes };
    }
    if (fn.type !== "member_expression") {
      return null;
    }
    const property = fn.childForFieldName("property");
    const object = fn.childForFieldName("object");
    if (property?.type === "property_identifier") {
      attributes.push(property.text);
    }
    if (object?.type !== "call_expression") {
      return null;
    }
    current = object;
  }
  return null;
}

function expressionSpineAttributes(node) {
  const attributes = [];
  let current = node;
  while (current) {
    if (current.type === "call_expression") {
      current = current.childForFieldName("function");
      continue;
    }
    if (current.type !== "member_expression") {
      break;
    }
    const property = current.childForFieldName("property");
    if (property?.type === "property_identifier") {
      attributes.push(property.text);
    }
    current = current.childForFieldName("object");
  }
  return attributes;
}

function componentCallInfo(result, node, stack) {
  const info = callChainInfo(node);
  if (!info || !isComponentName(info.name)) {
    return null;
  }

  const statement = node.parent;
  const container = statement?.type === "expression_statement" ? statement.parent : null;
  const host = stack.length > 0 ? stack[stack.length - 1] : null;
  const inArkuiChildren = container?.type === "arkui_children";
  const atDeclarativeRoot = (
    container?.type === "statement_block"
    && (host?.kind === "build_method" || host?.kind === "builder")
  );
  const isPageTransition = (
    container?.type === "statement_block"
    && PAGE_TRANSITION_COMPONENTS.has(info.name)
  );
  const isDialogBuilder = (
    isCustomDialogBuilderValue(node)
    && result.ui_structs.has(info.name)
  );
  if (!inArkuiChildren && !atDeclarativeRoot && !isPageTransition && !isDialogBuilder) {
    return null;
  }
  return info;
}

function detachedComponentInfo(node) {
  if (
    node.type !== "identifier"
    || !isComponentName(node.text)
    || node.parent?.type !== "expression_statement"
    || node.parent.parent?.type !== "arkui_children"
    || node.parent.text.trim() !== node.text
  ) {
    return null;
  }

  const continuation = nextNonCommentSibling(node.parent);
  if (!isArgumentsContinuation(continuation) || !positionsTouch(node.parent, continuation)) {
    return null;
  }

  const attributes = expressionSpineAttributes(firstNamedChild(continuation));
  const continuationInfo = componentContinuationInfo(continuation);
  attributes.push(...continuationInfo.attributes);
  const endNode = continuationInfo.endNode;

  const declarationSpan = span(node);
  declarationSpan.end_line = endNode.endPosition.row + 1;
  declarationSpan.end_col = endNode.endPosition.column + 1;
  return { name: node.text, attributes, span: declarationSpan };
}

function collectUiStructNames(node, result) {
  if (
    node.type === "struct_declaration"
    && UI_STRUCT_DECORATORS.some((decorator) => hasDecorator(node, decorator))
  ) {
    const name = childText(node, "name");
    if (name) {
      result.ui_structs.add(name);
    }
  }
  for (let index = 0; index < node.childCount; index += 1) {
    collectUiStructNames(node.child(index), result);
  }
}

function addDeclaration(result, node, kind, name, stack, declarationSpan = span(node)) {
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
    span: declarationSpan,
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
    let kind = "method";
    if (name === "build") {
      kind = "build_method";
    } else if (hasDecorator(node, "@Builder")) {
      kind = "builder";
    }
    if (/^\s*async\b/.test(node.text)) {
      result.syntax.add("async_fn");
    }
    return addDeclaration(result, node, kind, name, stack);
  }
  if (node.type === "arkui_component_expression") {
    const name = childText(node, "function");
    if (isComponentName(name)) {
      result.components.add(name);
      return addDeclaration(result, node, "ui_block", name, stack, arkuiComponentSpan(node));
    }
  }
  if (node.type === "call_expression") {
    const info = componentCallInfo(result, node, stack);
    if (info) {
      result.components.add(info.name);
      for (const attribute of info.attributes) {
        result.attributes.add(attribute);
      }
      collectTrailingModifierAttributes(result, node);
      return addDeclaration(
        result,
        node,
        "ui_block",
        info.name,
        stack,
        arkuiComponentSpan(node),
      );
    }
  }
  if (node.type === "identifier") {
    const info = detachedComponentInfo(node);
    if (info) {
      result.components.add(info.name);
      for (const attribute of info.attributes) {
        result.attributes.add(attribute);
      }
      return addDeclaration(result, node, "ui_block", info.name, stack, info.span);
    }
  }
  return null;
}

function collectTrailingModifierAttributes(result, node) {
  const info = componentContinuationInfo(node.parent);
  for (const attribute of info.attributes) {
    result.attributes.add(attribute);
  }
}

function collectArkuiAttributes(result, node) {
  for (let index = 0; index < node.childCount; index += 1) {
    const child = node.child(index);
    const field = node.fieldNameForChild(index);
    if (field === "property" && child.type === "property_identifier") {
      result.attributes.add(child.text);
    }
  }
  collectTrailingModifierAttributes(result, node);
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

  if (
    node.type === "arkui_component_expression"
    && isComponentName(childText(node, "function"))
  ) {
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
    ui_structs: new Set(),
  };

  collectUiStructNames(tree.rootNode, result);
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
