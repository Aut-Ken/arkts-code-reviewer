# `has_lifecycle` exact-Tag contract v1

Judge whether the changed ReviewUnit contains exact, owner-qualified ArkUI lifecycle behavior.
This is a Unit-level applicability label, not a claim that the code is correct or incorrect.

A positive label requires an exact lifecycle method owned by the changed Unit:

- `aboutToAppear` or `aboutToDisappear` on a custom component declared with `@Component` or
  `@ComponentV2`;
- `onPageShow`, `onPageHide` or `onBackPress` on an `@Entry` component that acts as a router page.

The method must be the changed method itself or a direct method child of the changed component
ReviewUnit. An enclosing component does not transfer lifecycle ownership to a nested ordinary
class.

Use a negative label for same-named methods on ordinary classes, undecorated structs,
`@CustomDialog`, or non-entry components where the page lifecycle contract does not apply. Also
use negative for `onReady`, attribute callbacks, strings/comments, and sibling methods that only
share a file with lifecycle code.

Use `needs_taxonomy_decision` when the supplied source cannot determine the semantic owner or the
case falls outside this contract. Do not guess a positive label from a filename, source-family
name, or file-level occurrence.
