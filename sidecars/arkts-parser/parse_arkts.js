#!/usr/bin/env node
"use strict";

const fs = require("fs");
const Parser = require("tree-sitter");
const ArkTS = require("tree-sitter-arkts");

const MIN_BUFFER_SIZE = 256 * 1024;
const FILE_ANALYSIS_PRODUCER_VERSION = "arkts-parser-sidecar-v2.0.0";
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
  const options = { path: "<stdin>", outputSchema: "code-facts-v1" };
  for (let index = 2; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--path" && index + 1 < argv.length) {
      options.path = argv[index + 1];
      index += 1;
    } else if (arg === "--output-schema" && index + 1 < argv.length) {
      options.outputSchema = argv[index + 1];
      index += 1;
    }
  }
  if (!new Set(["code-facts-v1", "file-analysis-v1"]).has(options.outputSchema)) {
    throw new Error(`unsupported output schema: ${options.outputSchema}`);
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

function v2LineSpan(startNode, endNode = startNode) {
  return {
    start_line: startNode.startPosition.row + 1,
    end_line: endNode.endPosition.row + 1,
  };
}

function v2Ref(kind, localId) {
  return { kind, local_id: localId };
}

function v2SafeIdPart(value) {
  return encodeURIComponent(String(value));
}

function sameNodeRange(left, right) {
  return Boolean(
    left
    && right
    && left.type === right.type
    && left.startIndex === right.startIndex
    && left.endIndex === right.endIndex
  );
}

function previousAttachedStartNode(node) {
  let current = node.previousNamedSibling;
  while (current?.type === "comment") {
    current = current.previousNamedSibling;
  }
  if (current?.type !== "decorator") {
    return node;
  }
  let earliest = current;
  current = current.previousNamedSibling;
  while (current?.type === "decorator") {
    earliest = current;
    current = current.previousNamedSibling;
  }
  return earliest;
}

function expressionSpineAttributeNodes(node) {
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
      attributes.push(property);
    }
    current = current.childForFieldName("object");
  }
  return attributes;
}

function leadingDotModifierNode(statement) {
  const leading = firstNamedChild(statement);
  if (leading?.type !== "leading_dot_expression") {
    return null;
  }
  const expression = leading.childForFieldName("expression") || firstNamedChild(leading);
  if (!expression) {
    return null;
  }
  if (expression.type === "identifier" || expression.type === "property_identifier") {
    return expression;
  }
  if (expression.type === "call_expression") {
    const fn = expression.childForFieldName("function");
    if (fn?.type === "identifier" || fn?.type === "property_identifier") {
      return fn;
    }
  }
  return null;
}

function componentContinuationDetails(statement) {
  const attributeNodes = [];
  let endNode = statement;
  while (endNode?.type === "expression_statement") {
    const next = nextNonCommentSibling(endNode);
    const modifierNode = leadingDotModifierNode(next);
    if (modifierNode) {
      const argumentsStatement = nextNonCommentSibling(next);
      if (
        !isArgumentsContinuation(argumentsStatement)
        || !positionsTouch(next, argumentsStatement)
      ) {
        break;
      }
      attributeNodes.push(modifierNode);
      attributeNodes.push(
        ...expressionSpineAttributeNodes(firstNamedChild(argumentsStatement)),
      );
      endNode = argumentsStatement;
      continue;
    }

    if (
      isArgumentsContinuation(next)
      && positionsTouch(endNode, next)
      && /\.[A-Za-z_$][A-Za-z0-9_$]*$/.test(endNode.text.trimEnd())
    ) {
      attributeNodes.push(...expressionSpineAttributeNodes(firstNamedChild(endNode)));
      attributeNodes.push(...expressionSpineAttributeNodes(firstNamedChild(next)));
      endNode = next;
      continue;
    }
    break;
  }
  return { attributeNodes, endNode };
}

function callChainDetails(node) {
  if (node.type !== "call_expression") {
    return null;
  }
  const attributeNodes = [];
  let current = node;
  while (current?.type === "call_expression") {
    const fn = current.childForFieldName("function");
    if (!fn) {
      return null;
    }
    if (fn.type === "identifier") {
      return { name: fn.text, nameNode: fn, attributeNodes };
    }
    if (fn.type !== "member_expression") {
      return null;
    }
    const property = fn.childForFieldName("property");
    const object = fn.childForFieldName("object");
    if (property?.type === "property_identifier") {
      attributeNodes.push(property);
    }
    if (object?.type !== "call_expression") {
      return null;
    }
    current = object;
  }
  return null;
}

function uniqueNodes(nodes) {
  const byRange = new Map();
  for (const node of nodes) {
    if (!node) {
      continue;
    }
    byRange.set(`${node.type}:${node.startIndex}:${node.endIndex}`, node);
  }
  return Array.from(byRange.values()).sort(
    (left, right) => left.startIndex - right.startIndex || left.endIndex - right.endIndex,
  );
}

function v2DeclarationStartNode(node, kind) {
  if (new Set(["struct", "class", "function", "method", "build_method", "builder"])
    .has(kind)) {
    return previousAttachedStartNode(node);
  }
  return node;
}

function v2AddDeclaration(
  analysis,
  node,
  kind,
  name,
  stack,
  details = {},
) {
  if (!name) {
    return null;
  }
  const parent = stack.length > 0 ? stack[stack.length - 1] : null;
  const qualifiedName = parent ? `${parent.qualified_name}.${name}` : name;
  const startNode = details.startNode || v2DeclarationStartNode(node, kind);
  const endNode = details.endNode || node;
  const startOffset = startNode.startIndex;
  const endOffset = endNode.endIndex;
  const localId = [
    "declaration",
    kind,
    `${startOffset}-${endOffset}`,
    v2SafeIdPart(qualifiedName),
  ].join(":");
  const declaration = {
    local_id: localId,
    kind,
    name,
    qualified_name: qualifiedName,
    span: v2LineSpan(startNode, endNode),
    start_offset: startOffset,
    end_offset: endOffset,
    parent: parent ? v2Ref("declaration", parent.local_id) : null,
    _node: node,
    _nameNode: details.nameNode
      || details.componentNameNode
      || node.childForFieldName("name"),
    _componentNameNode: details.componentNameNode || null,
    _attributeNodes: uniqueNodes(details.attributeNodes || []),
  };
  analysis.declarations.push(declaration);
  return declaration;
}

function directArkuiAttributeNodes(node) {
  const nodes = [];
  for (let index = 0; index < node.childCount; index += 1) {
    const child = node.child(index);
    if (
      node.fieldNameForChild(index) === "property"
      && child.type === "property_identifier"
    ) {
      nodes.push(child);
    }
  }
  return nodes;
}

function v2DeclarationForNode(analysis, legacyResult, node, stack) {
  if (node.type === "struct_declaration") {
    return v2AddDeclaration(
      analysis,
      node,
      "struct",
      childText(node, "name"),
      stack,
    );
  }
  if (node.type === "class_declaration" || node.type === "abstract_class_declaration") {
    return v2AddDeclaration(
      analysis,
      node,
      "class",
      childText(node, "name"),
      stack,
    );
  }
  if (node.type === "function_declaration" || node.type === "generator_function_declaration") {
    const kind = hasDecorator(node, "@Builder") ? "builder" : "function";
    return v2AddDeclaration(
      analysis,
      node,
      kind,
      childText(node, "name"),
      stack,
    );
  }
  if (node.type === "method_definition") {
    const name = childText(node, "name");
    let kind = "method";
    if (name === "build") {
      kind = "build_method";
    } else if (hasDecorator(node, "@Builder")) {
      kind = "builder";
    }
    return v2AddDeclaration(analysis, node, kind, name, stack);
  }
  if (node.type === "arkui_component_expression") {
    const nameNode = node.childForFieldName("function");
    if (nameNode && isComponentName(nameNode.text)) {
      const continuation = componentContinuationDetails(node.parent);
      return v2AddDeclaration(analysis, node, "ui_block", nameNode.text, stack, {
        endNode: continuation.endNode || node,
        componentNameNode: nameNode,
        attributeNodes: directArkuiAttributeNodes(node).concat(
          continuation.attributeNodes,
        ),
      });
    }
  }
  if (node.type === "call_expression") {
    const info = componentCallInfo(legacyResult, node, stack);
    const details = callChainDetails(node);
    if (info && details) {
      const continuation = componentContinuationDetails(node.parent);
      return v2AddDeclaration(analysis, node, "ui_block", info.name, stack, {
        endNode: continuation.endNode || node,
        componentNameNode: details.nameNode,
        attributeNodes: details.attributeNodes.concat(continuation.attributeNodes),
      });
    }
  }
  if (node.type === "identifier") {
    const info = detachedComponentInfo(node);
    if (info) {
      const initialContinuation = nextNonCommentSibling(node.parent);
      const continuation = componentContinuationDetails(initialContinuation);
      return v2AddDeclaration(analysis, node, "ui_block", info.name, stack, {
        endNode: continuation.endNode || initialContinuation || node,
        componentNameNode: node,
        attributeNodes: expressionSpineAttributeNodes(
          firstNamedChild(initialContinuation),
        ).concat(continuation.attributeNodes),
      });
    }
  }
  return null;
}

function v2NearestHost(stack) {
  for (let index = stack.length - 1; index >= 0; index -= 1) {
    if (stack[index].kind === "struct" || stack[index].kind === "class") {
      return stack[index];
    }
  }
  return null;
}

function v2AddRegion(analysis, node, kind, symbol, owner, extra = {}) {
  const localId = [
    "region",
    kind,
    `${node.startIndex}-${node.endIndex}`,
    v2SafeIdPart(symbol),
  ].join(":");
  const region = {
    local_id: localId,
    kind,
    symbol,
    span: v2LineSpan(node),
    start_offset: node.startIndex,
    end_offset: node.endIndex,
    owner: owner ? v2Ref("declaration", owner.local_id) : null,
    _node: node,
    ...extra,
  };
  analysis.reviewRegions.push(region);
  return region;
}

function v2DiscoverStructures(node, analysis, legacyResult, stack) {
  if (node.type === "import_statement") {
    v2AddRegion(
      analysis,
      node,
      "import_region",
      `import@${node.startPosition.row + 1}`,
      null,
    );
  } else if (node.type === "public_field_definition") {
    const host = v2NearestHost(stack);
    const nameNode = node.childForFieldName("name");
    if (host && nameNode) {
      const symbol = `${host.qualified_name}.${nameNode.text}`;
      const region = v2AddRegion(
        analysis,
        node,
        "field_region",
        symbol,
        host,
        { _fieldName: nameNode.text, _hostLocalId: host.local_id },
      );
      if (!analysis.fieldRegionsByHost.has(host.local_id)) {
        analysis.fieldRegionsByHost.set(host.local_id, new Map());
      }
      analysis.fieldRegionsByHost.get(host.local_id).set(nameNode.text, region);
    }
  }

  const declaration = v2DeclarationForNode(
    analysis,
    legacyResult,
    node,
    stack,
  );
  const nextStack = declaration ? stack.concat([declaration]) : stack;
  for (let index = 0; index < node.childCount; index += 1) {
    v2DiscoverStructures(node.child(index), analysis, legacyResult, nextStack);
  }
}

function v2StructureSortKey(item) {
  return [item.start_offset, item.end_offset, item.kind, item.local_id];
}

function compareV2Items(left, right) {
  const leftKey = v2StructureSortKey(left);
  const rightKey = v2StructureSortKey(right);
  for (let index = 0; index < leftKey.length; index += 1) {
    if (leftKey[index] < rightKey[index]) {
      return -1;
    }
    if (leftKey[index] > rightKey[index]) {
      return 1;
    }
  }
  return 0;
}

function v2OwnerForRange(analysis, startOffset, endOffset) {
  const candidates = [];
  for (const declaration of analysis.declarations) {
    if (declaration.start_offset <= startOffset && endOffset <= declaration.end_offset) {
      candidates.push({ kind: "declaration", item: declaration });
    }
  }
  for (const region of analysis.reviewRegions) {
    if (region.start_offset <= startOffset && endOffset <= region.end_offset) {
      candidates.push({ kind: "region", item: region });
    }
  }
  candidates.sort((left, right) => {
    const leftWidth = left.item.end_offset - left.item.start_offset;
    const rightWidth = right.item.end_offset - right.item.start_offset;
    if (leftWidth !== rightWidth) {
      return leftWidth - rightWidth;
    }
    if (left.kind !== right.kind) {
      return left.kind === "region" ? -1 : 1;
    }
    return left.item.local_id.localeCompare(right.item.local_id);
  });
  const owner = candidates[0];
  return owner ? v2Ref(owner.kind, owner.item.local_id) : null;
}

function v2AddOccurrence(
  analysis,
  kind,
  name,
  node,
  canonicalName = null,
  explicitOwner = undefined,
  extra = {},
) {
  if (!node || !name) {
    return null;
  }
  const owner = explicitOwner === undefined
    ? v2OwnerForRange(analysis, node.startIndex, node.endIndex)
    : explicitOwner;
  const ownerKey = owner ? `${owner.kind}:${owner.local_id}` : "unresolved";
  const identity = [
    kind,
    name,
    canonicalName || "",
    node.startIndex,
    node.endIndex,
    ownerKey,
  ].join("\u0000");
  if (analysis.occurrenceIdentities.has(identity)) {
    return null;
  }
  analysis.occurrenceIdentities.add(identity);
  const localId = [
    "occurrence",
    kind,
    `${node.startIndex}-${node.endIndex}`,
    v2SafeIdPart(canonicalName || name),
  ].join(":");
  const occurrence = {
    local_id: localId,
    kind,
    name,
    canonical_name: canonicalName,
    span: v2LineSpan(node),
    start_offset: node.startIndex,
    end_offset: node.endIndex,
    owner,
    ...extra,
  };
  analysis.rawOccurrences.push(occurrence);
  return occurrence;
}

function firstChildWithText(node, text) {
  for (let index = 0; index < node.childCount; index += 1) {
    const child = node.child(index);
    if (child.text === text) {
      return child;
    }
  }
  return null;
}

function v2HostForRange(analysis, startOffset, endOffset) {
  return analysis.declarations
    .filter((item) => (
      (item.kind === "struct" || item.kind === "class")
      && item.start_offset <= startOffset
      && endOffset <= item.end_offset
    ))
    .sort((left, right) => (
      (left.end_offset - left.start_offset) - (right.end_offset - right.start_offset)
      || left.local_id.localeCompare(right.local_id)
    ))[0] || null;
}

function v2FieldAccessKinds(node) {
  const parent = node.parent;
  if (!parent) {
    return ["field_read"];
  }
  const left = parent.childForFieldName("left");
  if (sameNodeRange(left, node)) {
    if (parent.type === "augmented_assignment_expression") {
      return ["field_read", "field_write"];
    }
    if (parent.type === "assignment_expression") {
      return ["field_write"];
    }
  }
  if (parent.type === "update_expression") {
    return ["field_read", "field_write"];
  }
  return ["field_read"];
}

function v2ExtractFieldAccess(node, analysis) {
  if (node.type !== "member_expression") {
    return;
  }
  const object = node.childForFieldName("object");
  const property = node.childForFieldName("property");
  if (object?.type !== "this" || !property) {
    return;
  }
  const host = v2HostForRange(analysis, node.startIndex, node.endIndex);
  const fields = host ? analysis.fieldRegionsByHost.get(host.local_id) : null;
  if (!host || !fields?.has(property.text)) {
    return;
  }
  const canonicalName = `${host.qualified_name}.${property.text}`;
  for (const kind of v2FieldAccessKinds(node)) {
    v2AddOccurrence(
      analysis,
      kind,
      property.text,
      property,
      canonicalName,
    );
  }
}

function v2BindingNodeKey(node) {
  return `${node.type}:${node.startIndex}:${node.endIndex}`;
}

function v2BindingPatternNodes(node) {
  if (!node) {
    return [];
  }
  if (
    node.type === "identifier"
    || node.type === "type_identifier"
    || node.type === "shorthand_property_identifier_pattern"
  ) {
    return [node];
  }
  if (node.type === "pair_pattern") {
    return v2BindingPatternNodes(node.childForFieldName("value"));
  }
  if (node.type === "assignment_pattern") {
    return v2BindingPatternNodes(
      node.childForFieldName("left") || node.childForFieldName("name"),
    );
  }
  const result = [];
  for (let index = 0; index < node.namedChildCount; index += 1) {
    result.push(...v2BindingPatternNodes(node.namedChild(index)));
  }
  return result;
}

function v2Scope(startNode, endNode = startNode, ambiguous = false) {
  return {
    startOffset: startNode.startIndex,
    endOffset: endNode.endIndex,
    ambiguous,
  };
}

function v2NearestScopeNode(node, types) {
  let current = node.parent;
  while (current) {
    if (types.has(current.type)) {
      return current;
    }
    current = current.parent;
  }
  return null;
}

function v2CallableScope(node, rootNode) {
  const callable = v2NearestScopeNode(
    node,
    new Set([
      "function_declaration",
      "generator_function_declaration",
      "function_expression",
      "generator_function",
      "method_definition",
      "arrow_function",
    ]),
  );
  const body = callable?.childForFieldName("body");
  if (!callable || !body) {
    return null;
  }
  const parameters = callable.childForFieldName("parameters");
  return v2Scope(parameters || body, body);
}

function v2BlockScope(node, rootNode) {
  const block = v2NearestScopeNode(
    node,
    new Set(["statement_block", "switch_body", "arkui_children", "program"]),
  );
  return block ? v2Scope(block) : v2Scope(rootNode, rootNode, true);
}

function v2LoopHeaderScope(node) {
  const loop = v2NearestScopeNode(
    node,
    new Set(["for_statement", "for_in_statement", "for_of_statement"]),
  );
  if (!loop) {
    return null;
  }
  const body = loop.childForFieldName("body");
  return body && node.endIndex <= body.startIndex
    ? v2Scope(loop)
    : null;
}

function v2RecordBindingPattern(analysis, node, scope) {
  for (const bindingNode of v2BindingPatternNodes(node)) {
    if (!bindingNode.text) {
      continue;
    }
    if (!analysis.bindingRanges.has(bindingNode.text)) {
      analysis.bindingRanges.set(bindingNode.text, []);
    }
    analysis.bindingRanges.get(bindingNode.text).push(scope);
    analysis.bindingNodeKeys.add(v2BindingNodeKey(bindingNode));
  }
}

function v2CollectNonImportBindings(node, analysis, rootNode) {
  if (node.type === "required_parameter" || node.type === "optional_parameter") {
    const scope = v2CallableScope(node, rootNode);
    if (scope) {
      v2RecordBindingPattern(
        analysis,
        node.childForFieldName("pattern") || node.childForFieldName("name"),
        scope,
      );
    }
  } else if (node.type === "variable_declarator") {
    const declaration = node.parent;
    const keyword = declaration?.child(0)?.text;
    const loopScope = keyword === "var" ? null : v2LoopHeaderScope(node);
    const scope = loopScope || (
      keyword === "var"
        ? (v2CallableScope(node, rootNode) || v2Scope(rootNode))
        : v2BlockScope(node, rootNode)
    );
    v2RecordBindingPattern(analysis, node.childForFieldName("name"), scope);
  } else if (node.type === "catch_clause") {
    const body = node.childForFieldName("body");
    v2RecordBindingPattern(
      analysis,
      node.childForFieldName("parameter"),
      body ? v2Scope(body) : v2BlockScope(node, rootNode),
    );
  } else if (node.type === "for_in_statement" || node.type === "for_of_statement") {
    const kind = node.childForFieldName("kind")?.text;
    const scope = kind === "var"
      ? (v2CallableScope(node, rootNode) || v2Scope(rootNode))
      : v2Scope(node);
    v2RecordBindingPattern(analysis, node.childForFieldName("left"), scope);
  } else if (node.type === "arrow_function") {
    const parameters = node.childForFieldName("parameters");
    if (parameters?.type !== "formal_parameters") {
      const body = node.childForFieldName("body");
      v2RecordBindingPattern(
        analysis,
        parameters,
        body ? v2Scope(body) : v2Scope(node, node, true),
      );
    }
  } else if (
    node.type === "function_expression"
    || node.type === "generator_function"
    || node.type === "class_expression"
  ) {
    const body = node.childForFieldName("body");
    v2RecordBindingPattern(
      analysis,
      node.childForFieldName("name"),
      body ? v2Scope(body) : v2Scope(node, node, true),
    );
  } else if (
    new Set([
      "function_declaration",
      "generator_function_declaration",
      "class_declaration",
      "abstract_class_declaration",
      "struct_declaration",
      "interface_declaration",
      "enum_declaration",
      "type_alias_declaration",
    ]).has(node.type)
  ) {
    v2RecordBindingPattern(
      analysis,
      node.childForFieldName("name"),
      v2BlockScope(node, rootNode),
    );
  }
  for (let index = 0; index < node.childCount; index += 1) {
    v2CollectNonImportBindings(node.child(index), analysis, rootNode);
  }
}

function v2BindingStatus(analysis, name, offset) {
  const scopes = analysis.bindingRanges.get(name) || [];
  const visible = scopes.filter(
    (scope) => scope.startOffset <= offset && offset < scope.endOffset,
  );
  if (visible.some((scope) => scope.ambiguous)) {
    return "ambiguous";
  }
  return visible.length > 0 ? "shadowed" : "clear";
}

function v2CallRoot(name) {
  const match = /^([A-Za-z_$][A-Za-z0-9_$]*)/.exec(name);
  return match ? match[1] : null;
}

function v2FirstStringArgument(node) {
  const argumentsNode = node.childForFieldName("arguments");
  if (!argumentsNode) {
    return null;
  }
  for (let index = 0; index < argumentsNode.namedChildCount; index += 1) {
    const child = argumentsNode.namedChild(index);
    if (child.type === "string") {
      return child;
    }
  }
  return null;
}

function v2LiteralDisplay(text) {
  return text
    .replaceAll("\\", "\\\\")
    .replaceAll("\r", "\\r")
    .replaceAll("\n", "\\n")
    .replaceAll("\t", "\\t");
}

function v2ExtractNodeOccurrences(node, analysis) {
  if (node.type === "decorator") {
    v2AddOccurrence(
      analysis,
      "decorator",
      node.text,
      node,
      decoratorName(node.text),
    );
  } else if (node.type === "call_expression") {
    const fn = node.childForFieldName("function");
    if (fn) {
      const callName = normalizeCallText(fn.text);
      const callRoot = v2CallRoot(callName);
      v2AddOccurrence(
        analysis,
        "raw_call",
        callName,
        fn,
        null,
        undefined,
        {
          root_name: callRoot,
          binding_status: callRoot
            ? v2BindingStatus(analysis, callRoot, fn.startIndex)
            : "clear",
        },
      );
      if (callName === "$r" || callName === "$rawfile") {
        const resourceNode = v2FirstStringArgument(node);
        v2AddOccurrence(
          analysis,
          "resource_reference",
          callName,
          node,
          resourceNode ? v2UnquoteString(resourceNode.text) : null,
          undefined,
          {
            binding_status: v2BindingStatus(
              analysis,
              callName,
              fn.startIndex,
            ),
          },
        );
      }
    }
  } else if (
    (node.type === "string" && /^["']/.test(node.text))
    || (node.type === "template_string" && node.text.startsWith("`"))
  ) {
    v2AddOccurrence(analysis, "string_literal", v2LiteralDisplay(node.text), node);
  } else if (node.type === "await_expression") {
    const awaitNode = firstChildWithText(node, "await") || node;
    v2AddOccurrence(analysis, "syntax", "await", awaitNode, "await_expr");
  } else if (node.type === "arrow_function") {
    const arrowNode = firstChildWithText(node, "=>") || node;
    v2AddOccurrence(analysis, "syntax", "=>", arrowNode, "arrow_fn");
  } else if (node.type === "try_statement") {
    const tryNode = firstChildWithText(node, "try") || node;
    v2AddOccurrence(analysis, "syntax", "try", tryNode, "try_catch");
  } else if (
    node.text === "Promise"
    && node.namedChildCount === 0
    && new Set(["identifier", "type_identifier", "property_identifier"]).has(node.type)
  ) {
    v2AddOccurrence(analysis, "syntax", "Promise", node, "promise");
  }

  if (
    new Set([
      "function_declaration",
      "generator_function_declaration",
      "method_definition",
    ]).has(node.type)
  ) {
    const asyncNode = firstChildWithText(node, "async");
    if (asyncNode) {
      v2AddOccurrence(analysis, "syntax", "async", asyncNode, "async_fn");
    }
  }

  v2ExtractFieldAccess(node, analysis);
  for (let index = 0; index < node.childCount; index += 1) {
    v2ExtractNodeOccurrences(node.child(index), analysis);
  }
}

function v2UnquoteString(text) {
  if (text.length >= 2 && new Set(["'", "\""]).has(text[0])) {
    return text.slice(1, -1);
  }
  return text;
}

function v2ImportStatementBody(node) {
  for (let index = 0; index < node.namedChildCount; index += 1) {
    const child = node.namedChild(index);
    if (child.type === "lazy_import_statement") {
      return child;
    }
  }
  return node;
}

function v2ImportBindings(node) {
  const body = v2ImportStatementBody(node);
  const sourceNode = body.childForFieldName("source") || node.childForFieldName("source");
  const module = sourceNode ? v2UnquoteString(sourceNode.text) : "";
  let clause = null;
  for (let index = 0; index < body.namedChildCount; index += 1) {
    const child = body.namedChild(index);
    if (child.type === "import_clause") {
      clause = child;
      break;
    }
  }
  if (!clause || !module) {
    return [];
  }

  const bindings = [];
  for (let index = 0; index < clause.namedChildCount; index += 1) {
    const child = clause.namedChild(index);
    if (child.type === "identifier") {
      bindings.push({ node: child, localName: child.text, importedName: "default" });
    } else if (child.type === "namespace_import") {
      const localNode = firstNamedChild(child);
      if (localNode) {
        bindings.push({ node: localNode, localName: localNode.text, importedName: "*" });
      }
    } else if (child.type === "named_imports") {
      for (let specifierIndex = 0;
        specifierIndex < child.namedChildCount;
        specifierIndex += 1) {
        const specifier = child.namedChild(specifierIndex);
        if (specifier.type !== "import_specifier") {
          continue;
        }
        const importedNode = specifier.childForFieldName("name");
        const aliasNode = specifier.childForFieldName("alias");
        const localNode = aliasNode || importedNode;
        if (importedNode && localNode) {
          bindings.push({
            node: localNode,
            localName: localNode.text,
            importedName: importedNode.text,
          });
        }
      }
    }
  }
  return bindings.map((binding) => ({ ...binding, module }));
}

function v2ExtractImportBindings(analysis) {
  for (const region of analysis.reviewRegions) {
    if (region.kind !== "import_region") {
      continue;
    }
    const owner = v2Ref("region", region.local_id);
    for (const binding of v2ImportBindings(region._node)) {
      const occurrence = v2AddOccurrence(
        analysis,
        "import_binding",
        binding.localName,
        binding.node,
        `${binding.module}#${binding.importedName}`,
        owner,
        {
          module: binding.module,
          imported_name: binding.importedName,
          local_name: binding.localName,
        },
      );
      if (occurrence) {
        analysis.importBindings.push({
          localName: binding.localName,
          canonicalName: `${binding.module}#${binding.importedName}`,
          bindingLocalId: occurrence.local_id,
          regionLocalId: region.local_id,
        });
      }
    }
  }
}

function v2HasAncestor(node, type) {
  let current = node.parent;
  while (current) {
    if (current.type === type) {
      return true;
    }
    current = current.parent;
  }
  return false;
}

function v2ExtractImportUses(node, analysis, bindingsByName) {
  if (
    new Set([
      "identifier",
      "type_identifier",
      "shorthand_property_identifier",
    ]).has(node.type)
    && bindingsByName.has(node.text)
    && !v2HasAncestor(node, "import_statement")
    && !analysis.bindingNodeKeys.has(v2BindingNodeKey(node))
  ) {
    const binding = bindingsByName.get(node.text);
    v2AddOccurrence(
      analysis,
      "import_use",
      node.text,
      node,
      binding.canonicalName,
      undefined,
      {
        binding_local_id: binding.bindingLocalId,
        binding_region_local_id: binding.regionLocalId,
        binding_status: v2BindingStatus(analysis, node.text, node.startIndex),
      },
    );
  }
  for (let index = 0; index < node.childCount; index += 1) {
    v2ExtractImportUses(node.child(index), analysis, bindingsByName);
  }
}

function v2ExtractDeclarationSymbols(analysis) {
  for (const declaration of analysis.declarations) {
    if (!declaration._nameNode) {
      continue;
    }
    v2AddOccurrence(
      analysis,
      "symbol",
      declaration.name,
      declaration._nameNode,
      declaration.qualified_name,
      v2Ref("declaration", declaration.local_id),
    );
  }
}

function v2ExtractComponentFacts(analysis) {
  for (const declaration of analysis.declarations) {
    if (declaration.kind !== "ui_block" || !declaration._componentNameNode) {
      continue;
    }
    const owner = v2Ref("declaration", declaration.local_id);
    v2AddOccurrence(
      analysis,
      "component",
      declaration.name,
      declaration._componentNameNode,
      declaration.name,
      owner,
    );
    for (const attributeNode of declaration._attributeNodes) {
      v2AddOccurrence(
        analysis,
        "attribute",
        attributeNode.text,
        attributeNode,
        attributeNode.text,
        owner,
      );
    }
  }
}

function v2DiagnosticEntry(prefix, node, analysis, ordinal) {
  return {
    local_id: [
      prefix,
      `${node.startIndex}-${node.endIndex}`,
      v2SafeIdPart(node.type),
      ordinal,
    ].join(":"),
    kind: prefix === "error" ? "error_node" : "missing_node",
    node_type: node.type,
    span: v2LineSpan(node),
    start_offset: node.startIndex,
    end_offset: node.endIndex,
    owner: v2OwnerForRange(analysis, node.startIndex, node.endIndex),
  };
}

function v2ExtractDiagnostics(node, analysis) {
  if (node.type === "ERROR") {
    analysis.errorSpans.push(
      v2DiagnosticEntry("error", node, analysis, analysis.errorSpans.length + 1),
    );
  }
  if (node.isMissing) {
    analysis.missingSpans.push(
      v2DiagnosticEntry("missing", node, analysis, analysis.missingSpans.length + 1),
    );
  }
  for (let index = 0; index < node.childCount; index += 1) {
    v2ExtractDiagnostics(node.child(index), analysis);
  }
}

function v2PublicDeclaration(item) {
  return {
    local_id: item.local_id,
    kind: item.kind,
    name: item.name,
    qualified_name: item.qualified_name,
    span: item.span,
    start_offset: item.start_offset,
    end_offset: item.end_offset,
    parent: item.parent,
  };
}

function v2PublicRegion(item) {
  return {
    local_id: item.local_id,
    kind: item.kind,
    symbol: item.symbol,
    span: item.span,
    start_offset: item.start_offset,
    end_offset: item.end_offset,
    owner: item.owner,
  };
}

function buildFileAnalysisSnapshot(rootNode, legacyResult) {
  const analysis = {
    declarations: [],
    reviewRegions: [],
    rawOccurrences: [],
    errorSpans: [],
    missingSpans: [],
    fieldRegionsByHost: new Map(),
    importBindings: [],
    bindingRanges: new Map(),
    bindingNodeKeys: new Set(),
    occurrenceIdentities: new Set(),
  };
  v2DiscoverStructures(rootNode, analysis, legacyResult, []);
  analysis.declarations.sort(compareV2Items);
  analysis.reviewRegions.sort(compareV2Items);
  v2CollectNonImportBindings(rootNode, analysis, rootNode);
  v2ExtractComponentFacts(analysis);
  v2ExtractImportBindings(analysis);
  v2ExtractDeclarationSymbols(analysis);
  v2ExtractNodeOccurrences(rootNode, analysis);
  const bindingsByName = new Map(
    analysis.importBindings.map((binding) => [binding.localName, binding]),
  );
  v2ExtractImportUses(rootNode, analysis, bindingsByName);
  v2ExtractDiagnostics(rootNode, analysis);
  analysis.rawOccurrences.sort(compareV2Items);
  analysis.errorSpans.sort(compareV2Items);
  analysis.missingSpans.sort(compareV2Items);
  return analysis;
}

function toFileAnalysisOutput(legacyOutput, analysis) {
  return {
    output_schema: "file-analysis-v1",
    producer_version: FILE_ANALYSIS_PRODUCER_VERSION,
    offset_unit: "utf16_code_unit",
    ...legacyOutput,
    declarations_v2: analysis.declarations.map(v2PublicDeclaration),
    review_regions: analysis.reviewRegions.map(v2PublicRegion),
    raw_occurrences: analysis.rawOccurrences,
    error_spans: analysis.errorSpans,
    missing_spans: analysis.missingSpans,
  };
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
  const legacyOutput = toOutput(result, options.path, tree.rootNode.type);
  const output = options.outputSchema === "file-analysis-v1"
    ? toFileAnalysisOutput(
      legacyOutput,
      buildFileAnalysisSnapshot(tree.rootNode, result),
    )
    : legacyOutput;
  process.stdout.write(`${JSON.stringify(output)}\n`);
}

try {
  main();
} catch (error) {
  process.stdout.write(JSON.stringify({
    error: error && error.message ? error.message : String(error),
  }) + "\n");
  process.exitCode = 1;
}
