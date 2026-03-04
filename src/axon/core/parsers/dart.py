"""Dart / Flutter language parser using tree-sitter.

Extracts classes, methods, top-level functions, imports, call expressions,
type annotation references, and class heritage (extends / implements / with)
from Dart source files.
"""

from __future__ import annotations

from tree_sitter import Language, Node, Parser
from tree_sitter_language_pack import get_language as get_ts_language

from axon.core.parsers.base import (
    CallInfo,
    ImportInfo,
    LanguageParser,
    ParseResult,
    SymbolInfo,
    TypeRef,
)

DART_LANGUAGE: Language = get_ts_language("dart")

_BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        "int",
        "double",
        "num",
        "String",
        "bool",
        "void",
        "dynamic",
        "Object",
        "Null",
        "Never",
        "var",
        "Future",
        "Stream",
        "List",
        "Map",
        "Set",
        "Iterable",
    }
)


class DartParser(LanguageParser):
    """Parses Dart / Flutter source files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(DART_LANGUAGE)

    def parse(self, content: str, file_path: str) -> ParseResult:
        tree = self._parser.parse(content.encode("utf-8"))
        result = ParseResult()
        # walk_visited prevents _walk from re-entering already-handled subtrees.
        # Call extraction uses its own fresh set per body node (tree has no cycles).
        walk_visited: set[int] = set()
        self._walk(tree.root_node, content, result, class_name="", visited=walk_visited)
        return result

    # ------------------------------------------------------------------
    # AST walking
    # ------------------------------------------------------------------

    def _walk(
        self,
        node: Node,
        source: str,
        result: ParseResult,
        class_name: str,
        visited: set[int],
    ) -> None:
        if node.id in visited:
            return
        visited.add(node.id)

        children = node.children
        i = 0
        while i < len(children):
            child = children[i]
            ntype = child.type

            if ntype == "import_or_export":
                self._extract_import(child, result)

            elif ntype == "class_definition":
                self._extract_class(child, source, result, visited)

            elif ntype == "mixin_declaration":
                self._extract_mixin_declaration(child, source, result, visited)

            elif ntype == "extension_declaration":
                self._extract_extension_declaration(child, source, result, visited)

            elif ntype == "type_alias":
                self._extract_typedef(child, result)

            elif ntype == "enum_declaration":
                self._extract_enum(child, result)

            elif ntype == "function_signature":
                # Top-level function: signature followed by function_body
                body_node = (
                    children[i + 1]
                    if i + 1 < len(children) and children[i + 1].type == "function_body"
                    else None
                )
                self._extract_function(child, body_node, source, result, class_name)
                if body_node:
                    visited.add(body_node.id)
                    self._extract_calls_recursive(body_node, result, set())
                    i += 1  # skip the body node

            elif ntype == "declaration":
                self._extract_calls_recursive(child, result, set())

            else:
                if child.id not in visited:
                    self._walk(child, source, result, class_name, visited)

            i += 1

    # ------------------------------------------------------------------
    # Classes
    # ------------------------------------------------------------------

    def _extract_class(
        self, node: Node, source: str, result: ParseResult, visited: set[int]
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        content = node.text.decode("utf-8")

        # Check for abstract modifier
        is_abstract = any(c.type == "abstract" for c in node.children)
        kind = "abstract_class" if is_abstract else "class"

        result.symbols.append(
            SymbolInfo(
                name=name,
                kind=kind,
                start_line=start_line,
                end_line=end_line,
                content=content,
            )
        )

        # Heritage: extends, implements, with (mixins)
        # Mixins live inside the superclass node — only extract there (no second loop).
        superclass = node.child_by_field_name("superclass")
        if superclass is not None:
            for child in superclass.children:
                if child.type == "type_identifier":
                    result.heritage.append((name, "extends", child.text.decode("utf-8")))
                elif child.type == "mixins":
                    for mixin_child in child.children:
                        if mixin_child.type == "type_identifier":
                            result.heritage.append(
                                (name, "with", mixin_child.text.decode("utf-8"))
                            )

        interfaces = node.child_by_field_name("interfaces")
        if interfaces is not None:
            for child in interfaces.children:
                if child.type == "type_identifier":
                    result.heritage.append(
                        (name, "implements", child.text.decode("utf-8"))
                    )

        # Walk class body for methods/fields
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk_class_body(body, source, result, class_name=name, visited=visited)

    def _walk_class_body(
        self,
        body: Node,
        source: str,
        result: ParseResult,
        class_name: str,
        visited: set[int],
    ) -> None:
        """Walk a class_body or extension_body node."""
        children = body.children
        pending_annotations: list[str] = []
        i = 0
        while i < len(children):
            child = children[i]

            if child.type == "annotation":
                ann_name = self._annotation_name(child)
                if ann_name:
                    pending_annotations.append(ann_name)

            elif child.type == "method_signature":
                body_node = (
                    children[i + 1]
                    if i + 1 < len(children) and children[i + 1].type == "function_body"
                    else None
                )
                decorators = list(pending_annotations)
                pending_annotations = []

                self._extract_method_from_signature(
                    child,
                    body_node,
                    source,
                    result,
                    class_name,
                    decorators=decorators,
                    visited=visited,
                )
                if body_node:
                    self._extract_calls_recursive(body_node, result, set())
                    i += 1

            elif child.type == "declaration":
                pending_annotations = []
                self._extract_field_types(child, result)
                # Named constructors appear as declaration -> constructor_signature
                self._maybe_extract_named_constructor(
                    child, source, result, class_name
                )
                self._extract_calls_recursive(child, result, set())

            elif child.type == "function_signature":
                body_node = (
                    children[i + 1]
                    if i + 1 < len(children) and children[i + 1].type == "function_body"
                    else None
                )
                decorators = list(pending_annotations)
                pending_annotations = []

                self._extract_method_from_signature(
                    child,
                    body_node,
                    source,
                    result,
                    class_name,
                    decorators=decorators,
                    visited=visited,
                )
                if body_node:
                    self._extract_calls_recursive(body_node, result, set())
                    i += 1

            else:
                pending_annotations = []

            i += 1

    # ------------------------------------------------------------------
    # Mixin declarations
    # ------------------------------------------------------------------

    def _extract_mixin_declaration(
        self, node: Node, source: str, result: ParseResult, visited: set[int]
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            # Fallback: second identifier child (after 'mixin' keyword)
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        result.symbols.append(
            SymbolInfo(
                name=name,
                kind="mixin",
                start_line=start_line,
                end_line=end_line,
                content=node.text.decode("utf-8"),
            )
        )

        # on clause: `mixin Loggable on Widget`
        for child in node.children:
            if child.type == "type_identifier" and child is not name_node:
                result.heritage.append((name, "on", child.text.decode("utf-8")))

        # mixin_declaration has no 'body' field; body is class_body child.
        body = node.child_by_field_name("body")
        if body is None:
            for child in node.children:
                if child.type == "class_body":
                    body = child
                    break
        if body is not None:
            self._walk_class_body(body, source, result, class_name=name, visited=visited)

    # ------------------------------------------------------------------
    # Extension declarations
    # ------------------------------------------------------------------

    def _extract_extension_declaration(
        self, node: Node, source: str, result: ParseResult, visited: set[int]
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        result.symbols.append(
            SymbolInfo(
                name=name,
                kind="extension",
                start_line=start_line,
                end_line=end_line,
                content=node.text.decode("utf-8"),
            )
        )

        # on clause: `extension StringX on String`
        seen_on = False
        for child in node.children:
            if child.type == "on":
                seen_on = True
            elif seen_on and child.type == "type_identifier":
                result.heritage.append((name, "on", child.text.decode("utf-8")))
                seen_on = False

        body = node.child_by_field_name("body")
        if body is not None:
            self._walk_class_body(body, source, result, class_name=name, visited=visited)

    # ------------------------------------------------------------------
    # Typedef
    # ------------------------------------------------------------------

    def _extract_typedef(self, node: Node, result: ParseResult) -> None:
        # type_alias has no 'name' field; name is first type_identifier child.
        name_node = node.child_by_field_name("name")
        if name_node is None:
            for child in node.children:
                if child.type == "type_identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")
        result.symbols.append(
            SymbolInfo(
                name=name,
                kind="typedef",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                content=node.text.decode("utf-8"),
            )
        )

    # ------------------------------------------------------------------
    # Functions (top-level)
    # ------------------------------------------------------------------

    def _extract_function(
        self,
        sig_node: Node,
        body_node: Node | None,
        source: str,
        result: ParseResult,
        class_name: str,
    ) -> None:
        name_node = sig_node.child_by_field_name("name")
        if name_node is None:
            for child in sig_node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")

        if body_node:
            start_line = sig_node.start_point[0] + 1
            end_line = body_node.end_point[0] + 1
            content = source[sig_node.start_byte : body_node.end_byte]
        else:
            start_line = sig_node.start_point[0] + 1
            end_line = sig_node.end_point[0] + 1
            content = sig_node.text.decode("utf-8")

        signature = self._build_signature(sig_node, name)

        result.symbols.append(
            SymbolInfo(
                name=name,
                kind="function",
                start_line=start_line,
                end_line=end_line,
                content=content,
                signature=signature,
                class_name=class_name,
            )
        )

        self._extract_signature_types(sig_node, result)

    # ------------------------------------------------------------------
    # Methods (inside class)
    # ------------------------------------------------------------------

    def _extract_method_from_signature(
        self,
        sig_node: Node,
        body_node: Node | None,
        source: str,
        result: ParseResult,
        class_name: str,
        decorators: list[str] | None = None,
        visited: set[int] | None = None,
    ) -> None:
        """Extract a method from its signature node.

        Handles method_signature (which may contain function_signature,
        getter_signature, setter_signature, or factory_constructor_signature)
        and standalone function_signature nodes.
        """
        inner = sig_node
        kind = "method"

        if sig_node.type == "method_signature":
            for child in sig_node.children:
                if child.type in (
                    "function_signature",
                    "getter_signature",
                    "setter_signature",
                    "factory_constructor_signature",
                ):
                    inner = child
                    break

        if inner.type == "getter_signature":
            kind = "getter"
        elif inner.type == "setter_signature":
            kind = "setter"
        elif inner.type == "factory_constructor_signature":
            kind = "factory_constructor"

        # Extract name
        name: str = ""
        if inner.type == "factory_constructor_signature":
            # factory MyClass.named(...) — grab second identifier
            idents = [c for c in inner.children if c.type == "identifier"]
            if len(idents) >= 2:
                name = f"{idents[0].text.decode('utf-8')}.{idents[1].text.decode('utf-8')}"
            elif idents:
                name = idents[0].text.decode("utf-8")
        else:
            name_node = inner.child_by_field_name("name")
            if name_node is None:
                for child in inner.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None:
                name = name_node.text.decode("utf-8")

        if not name:
            return

        if body_node:
            start_line = sig_node.start_point[0] + 1
            end_line = body_node.end_point[0] + 1
            content = source[sig_node.start_byte : body_node.end_byte]
        else:
            start_line = sig_node.start_point[0] + 1
            end_line = sig_node.end_point[0] + 1
            content = sig_node.text.decode("utf-8")

        signature = self._build_signature(inner, name)

        result.symbols.append(
            SymbolInfo(
                name=name,
                kind=kind,
                start_line=start_line,
                end_line=end_line,
                content=content,
                signature=signature,
                class_name=class_name,
                decorators=decorators or [],
            )
        )

        self._extract_signature_types(inner, result)

    def _maybe_extract_named_constructor(
        self,
        decl_node: Node,
        source: str,
        result: ParseResult,
        class_name: str,
    ) -> None:
        """Extract named constructors from declaration -> constructor_signature."""
        for child in decl_node.children:
            if child.type == "constructor_signature":
                # constructor_signature: ClassName.named(params)
                # The 'name' field returns the class name identifier; the
                # named part is the third identifier (after the dot).
                idents = [c for c in child.children if c.type == "identifier"]
                if len(idents) >= 2:
                    ctor_name = (
                        f"{idents[0].text.decode('utf-8')}.{idents[1].text.decode('utf-8')}"
                    )
                elif idents:
                    ctor_name = idents[0].text.decode("utf-8")
                else:
                    continue

                result.symbols.append(
                    SymbolInfo(
                        name=ctor_name,
                        kind="constructor",
                        start_line=child.start_point[0] + 1,
                        end_line=decl_node.end_point[0] + 1,
                        content=decl_node.text.decode("utf-8"),
                        class_name=class_name,
                    )
                )

    # ------------------------------------------------------------------
    # Enums
    # ------------------------------------------------------------------

    def _extract_enum(self, node: Node, result: ParseResult) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")
        result.symbols.append(
            SymbolInfo(
                name=name,
                kind="enum",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                content=node.text.decode("utf-8"),
            )
        )

    # ------------------------------------------------------------------
    # Imports / Exports
    # ------------------------------------------------------------------

    def _extract_import(self, node: Node, result: ParseResult) -> None:
        """Extract import from ``import_or_export`` node."""
        for child in node.children:
            if child.type == "library_import":
                self._extract_library_import(child, result)
            elif child.type == "library_export":
                self._extract_library_export(child, result)

    def _extract_library_import(self, node: Node, result: ParseResult) -> None:
        """Extract ``import 'pkg' as alias show X, Y;``."""
        module = ""
        alias = ""
        names: list[str] = []

        for child in node.children:
            if child.type == "import_specification":
                saw_as = False
                for spec_child in child.children:
                    if spec_child.type == "configurable_uri":
                        module = self._extract_uri(spec_child)
                    elif spec_child.type == "as":
                        saw_as = True
                    elif saw_as and spec_child.type == "identifier":
                        alias = spec_child.text.decode("utf-8")
                        saw_as = False
                    elif spec_child.type == "combinator":
                        for comb_child in spec_child.children:
                            if comb_child.type == "identifier":
                                names.append(comb_child.text.decode("utf-8"))

        if module:
            result.imports.append(
                ImportInfo(
                    module=module,
                    names=names,
                    is_relative=module.startswith("."),
                    alias=alias,
                )
            )

    def _extract_library_export(self, node: Node, result: ParseResult) -> None:
        """Extract ``export 'package:...';`` as an import record."""
        module = ""
        for child in node.children:
            if child.type == "configurable_uri":
                module = self._extract_uri(child)
                break

        if module:
            result.imports.append(
                ImportInfo(
                    module=module,
                    names=[],
                    is_relative=module.startswith("."),
                )
            )

    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

    def _extract_calls_recursive(
        self, node: Node, result: ParseResult, visited: set[int]
    ) -> None:
        """Recursively walk *node* and extract all Dart call expressions.

        In Dart's tree-sitter grammar, function calls are represented as
        a sequence of sibling nodes:
          identifier [selector(unconditional_assignable_selector)]* selector(argument_part)

        We scan each node's children for this pattern and recurse into all
        children (including argument lists) to catch nested calls.
        """
        if node.id in visited:
            return
        visited.add(node.id)

        self._extract_calls_from_children(node.children, result)

        for child in node.children:
            self._extract_calls_recursive(child, result, visited)

    def _extract_calls_from_children(
        self, children: list[Node], result: ParseResult
    ) -> None:
        """Scan a sibling list for Dart call patterns and emit CallInfo entries."""
        i = 0
        while i < len(children):
            child = children[i]

            # A call chain starts with an identifier or 'super' keyword.
            if child.type in ("identifier", "super"):
                # Build dotted receiver chain, looking ahead at siblings.
                chain: list[str] = [
                    child.text.decode("utf-8") if child.type != "super" else "super"
                ]
                j = i + 1

                while j < len(children):
                    sib = children[j]

                    if sib.type == "selector":
                        has_dot_access = False
                        has_args = False

                        for sc in sib.children:
                            if sc.type == "unconditional_assignable_selector":
                                for usc in sc.children:
                                    if usc.type == "identifier":
                                        chain.append(usc.text.decode("utf-8"))
                                has_dot_access = True
                            elif sc.type == "argument_part":
                                has_args = True

                        if has_args:
                            # Found a call!
                            name = chain[-1]
                            receiver = ".".join(chain[:-1]) if len(chain) > 1 else ""
                            args = self._extract_args_from_selector(sib)
                            result.calls.append(
                                CallInfo(
                                    name=name,
                                    line=child.start_point[0] + 1,
                                    receiver=receiver,
                                    arguments=args,
                                )
                            )
                            j += 1
                            # Check for chained calls on the result: .then(...), etc.
                            while j < len(children) and children[j].type == "selector":
                                next_sib = children[j]
                                next_has_args = any(
                                    sc.type == "argument_part" for sc in next_sib.children
                                )
                                if next_has_args:
                                    dot_name = None
                                    for sc in next_sib.children:
                                        if sc.type == "unconditional_assignable_selector":
                                            for usc in sc.children:
                                                if usc.type == "identifier":
                                                    dot_name = usc.text.decode("utf-8")
                                    if dot_name:
                                        chain.append(dot_name)
                                        new_receiver = ".".join(chain[:-1])
                                        new_args = self._extract_args_from_selector(next_sib)
                                        result.calls.append(
                                            CallInfo(
                                                name=dot_name,
                                                line=next_sib.start_point[0] + 1,
                                                receiver=new_receiver,
                                                arguments=new_args,
                                            )
                                        )
                                j += 1
                            break
                        elif has_dot_access:
                            j += 1
                        else:
                            break

                    elif sib.type == "unconditional_assignable_selector":
                        # Direct sibling (e.g. super.initState pattern)
                        for usc in sib.children:
                            if usc.type == "identifier":
                                chain.append(usc.text.decode("utf-8"))
                        j += 1

                    else:
                        break

                i = j

            else:
                i += 1

    def _extract_args_from_selector(self, selector_node: Node) -> list[str]:
        """Extract bare identifier arguments from a selector containing argument_part."""
        identifiers: list[str] = []
        for sc in selector_node.children:
            if sc.type == "argument_part":
                for ac in sc.children:
                    if ac.type == "arguments":
                        for arg in ac.children:
                            if arg.type in ("argument", "named_argument"):
                                for val in arg.children:
                                    if val.type == "identifier":
                                        identifiers.append(val.text.decode("utf-8"))
        return identifiers

    # ------------------------------------------------------------------
    # Type references
    # ------------------------------------------------------------------

    def _extract_signature_types(self, sig_node: Node, result: ParseResult) -> None:
        """Extract parameter types and return type from a function/getter/setter signature."""
        # Return type: first type_identifier before the function name keyword
        for child in sig_node.children:
            if child.type == "type_identifier":
                type_name = child.text.decode("utf-8")
                if type_name not in _BUILTIN_TYPES:
                    result.type_refs.append(
                        TypeRef(
                            name=type_name,
                            kind="return",
                            line=child.start_point[0] + 1,
                        )
                    )
                break
            elif child.type in ("void_type", "get", "set", "factory"):
                break

        # Parameters
        for child in sig_node.children:
            if child.type == "formal_parameter_list":
                self._extract_param_types(child, result)
                break

    def _extract_param_types(self, params_node: Node, result: ParseResult) -> None:
        """Extract type references from function parameters."""
        for child in params_node.children:
            if child.type == "formal_parameter":
                self._extract_single_param_type(child, result)
            elif child.type == "optional_formal_parameters":
                for sub in child.children:
                    if sub.type == "formal_parameter":
                        self._extract_single_param_type(sub, result)

    def _extract_single_param_type(self, param: Node, result: ParseResult) -> None:
        """Extract type from a single parameter using tree-sitter field names."""
        name_node = param.child_by_field_name("name")
        param_name = name_node.text.decode("utf-8") if name_node is not None else ""

        # Look for type_identifier child (the declared type)
        for child in param.children:
            if child.type == "type_identifier":
                type_name = child.text.decode("utf-8")
                if type_name not in _BUILTIN_TYPES and type_name != param_name:
                    result.type_refs.append(
                        TypeRef(
                            name=type_name,
                            kind="param",
                            line=param.start_point[0] + 1,
                            param_name=param_name,
                        )
                    )
                return

    def _extract_field_types(self, decl_node: Node, result: ParseResult) -> None:
        """Extract type references from field/variable declarations."""
        for child in decl_node.children:
            if child.type == "type_identifier":
                type_name = child.text.decode("utf-8")
                if type_name not in _BUILTIN_TYPES:
                    result.type_refs.append(
                        TypeRef(
                            name=type_name,
                            kind="variable",
                            line=child.start_point[0] + 1,
                        )
                    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _annotation_name(annotation_node: Node) -> str:
        """Extract the name from an @annotation node."""
        name_node = annotation_node.child_by_field_name("name")
        if name_node is not None:
            return name_node.text.decode("utf-8")
        for child in annotation_node.children:
            if child.type == "identifier":
                return child.text.decode("utf-8")
        return ""

    @staticmethod
    def _extract_uri(configurable_uri: Node) -> str:
        """Extract the string from a configurable_uri node."""
        for child in configurable_uri.children:
            if child.type == "uri":
                for uri_child in child.children:
                    if uri_child.type == "string_literal":
                        return DartParser._string_value(uri_child)
        return ""

    @staticmethod
    def _string_value(node: Node) -> str:
        """Extract text content from a string_literal node."""
        text = node.text.decode("utf-8")
        if len(text) >= 2 and text[0] in ("'", '"') and text[-1] in ("'", '"'):
            return text[1:-1]
        return text

    @staticmethod
    def _build_signature(sig_node: Node, name: str) -> str:
        """Build a human-readable signature from a function/getter/setter signature node."""
        return_type = ""
        params_text = ""

        for child in sig_node.children:
            if child.type == "type_identifier":
                return_type = child.text.decode("utf-8")
            elif child.type == "void_type":
                return_type = "void"
            elif child.type == "formal_parameter_list":
                params_text = child.text.decode("utf-8")

        sig = ""
        if return_type:
            sig += return_type + " "
        sig += name + params_text
        return sig
