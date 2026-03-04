"""Go language parser using tree-sitter.

Extracts functions, methods, types (struct, interface), imports, call
expressions, type annotation references, and interface embedding from Go
source files.
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

GO_LANGUAGE: Language = get_ts_language("go")

_BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        "bool",
        "byte",
        "complex64",
        "complex128",
        "error",
        "float32",
        "float64",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "rune",
        "string",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "uintptr",
        "any",
    }
)


class GoParser(LanguageParser):
    """Parses Go source files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(GO_LANGUAGE)

    def parse(self, content: str, file_path: str) -> ParseResult:
        tree = self._parser.parse(content.encode("utf-8"))
        result = ParseResult()
        self._walk(tree.root_node, result)
        return result

    # ------------------------------------------------------------------
    # AST walking
    # ------------------------------------------------------------------

    def _walk(self, node: Node, result: ParseResult) -> None:
        for child in node.children:
            ntype = child.type

            if ntype == "function_declaration":
                self._extract_function(child, result)
            elif ntype == "method_declaration":
                self._extract_method(child, result)
            elif ntype == "type_declaration":
                self._extract_type_declaration(child, result)
            elif ntype == "import_declaration":
                self._extract_import(child, result)
            elif ntype == "var_declaration":
                self._extract_calls_recursive(child, result)
            else:
                self._walk(child, result)

    # ------------------------------------------------------------------
    # Functions
    # ------------------------------------------------------------------

    def _extract_function(self, node: Node, result: ParseResult) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        content = node.text.decode("utf-8")
        signature = self._build_function_signature(node, name)
        is_exported = name[0].isupper() if name else False

        result.symbols.append(
            SymbolInfo(
                name=name,
                kind="function",
                start_line=start_line,
                end_line=end_line,
                content=content,
                signature=signature,
            )
        )

        if is_exported:
            result.exports.append(name)

        self._extract_param_types(node, result)
        self._extract_return_types(node, result)

        body = node.child_by_field_name("body")
        if body:
            self._extract_calls_recursive(body, result)

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def _extract_method(self, node: Node, result: ParseResult) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        content = node.text.decode("utf-8")

        receiver_name = self._extract_receiver_type(node)
        signature = self._build_function_signature(node, name)
        is_exported = name[0].isupper() if name else False

        result.symbols.append(
            SymbolInfo(
                name=name,
                kind="method",
                start_line=start_line,
                end_line=end_line,
                content=content,
                signature=signature,
                class_name=receiver_name,
            )
        )

        if is_exported:
            result.exports.append(name)

        self._extract_param_types(node, result)
        self._extract_return_types(node, result)

        body = node.child_by_field_name("body")
        if body:
            self._extract_calls_recursive(body, result)

    def _extract_receiver_type(self, method_node: Node) -> str:
        """Extract the receiver struct name from a method declaration."""
        receiver = method_node.child_by_field_name("receiver")
        if receiver is None:
            return ""

        for child in receiver.children:
            if child.type == "parameter_declaration":
                type_node = child.child_by_field_name("type")
                if type_node is not None:
                    return self._unwrap_type_name(type_node)
        return ""

    # ------------------------------------------------------------------
    # Type declarations (struct, interface, type alias)
    # ------------------------------------------------------------------

    def _extract_type_declaration(self, node: Node, result: ParseResult) -> None:
        """Handle ``type X struct { ... }`` / ``type X interface { ... }`` etc."""
        for child in node.children:
            if child.type == "type_spec":
                self._extract_type_spec(child, result)

    def _extract_type_spec(self, node: Node, result: ParseResult) -> None:
        name_node = node.child_by_field_name("name")
        type_node = node.child_by_field_name("type")
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        content = node.text.decode("utf-8")
        is_exported = name[0].isupper() if name else False

        if type_node is not None and type_node.type == "struct_type":
            result.symbols.append(
                SymbolInfo(
                    name=name,
                    kind="class",  # map Go struct to "class" for graph consistency
                    start_line=start_line,
                    end_line=end_line,
                    content=content,
                )
            )
            self._extract_struct_field_types(type_node, result)
            self._extract_struct_embedding(name, type_node, result)

        elif type_node is not None and type_node.type == "interface_type":
            result.symbols.append(
                SymbolInfo(
                    name=name,
                    kind="interface",
                    start_line=start_line,
                    end_line=end_line,
                    content=content,
                )
            )
            self._extract_interface_embedding(name, type_node, result)

        else:
            # type alias: ``type ID int`` or ``type Handler func(...)``
            result.symbols.append(
                SymbolInfo(
                    name=name,
                    kind="type_alias",
                    start_line=start_line,
                    end_line=end_line,
                    content=content,
                )
            )

        if is_exported:
            result.exports.append(name)

    def _extract_struct_field_types(self, struct_node: Node, result: ParseResult) -> None:
        """Extract type references from struct field declarations."""
        for child in struct_node.children:
            if child.type == "field_declaration_list":
                for field in child.children:
                    if field.type == "field_declaration":
                        type_node = field.child_by_field_name("type")
                        if type_node is not None:
                            self._add_type_refs(
                                type_node, "variable", type_node.start_point[0] + 1, result
                            )

    def _extract_struct_embedding(
        self, struct_name: str, struct_node: Node, result: ParseResult
    ) -> None:
        """Extract embedded structs (anonymous fields) as heritage."""
        for child in struct_node.children:
            if child.type == "field_declaration_list":
                for field in child.children:
                    if field.type == "field_declaration":
                        # Embedded field: no name, just a type
                        name_node = field.child_by_field_name("name")
                        type_node = field.child_by_field_name("type")
                        if name_node is None and type_node is not None:
                            embedded_name = self._unwrap_type_name(type_node)
                            if embedded_name:
                                result.heritage.append(
                                    (struct_name, "extends", embedded_name)
                                )

    def _extract_interface_embedding(
        self, iface_name: str, iface_node: Node, result: ParseResult
    ) -> None:
        """Extract embedded interfaces as heritage.

        In tree-sitter-go, embedded interface identifiers appear as
        ``type_elem`` nodes whose sole child is a ``type_identifier`` or
        ``qualified_type``.
        """
        for child in iface_node.children:
            if child.type == "type_elem":
                for elem_child in child.children:
                    if elem_child.type == "type_identifier":
                        result.heritage.append(
                            (iface_name, "extends", elem_child.text.decode("utf-8"))
                        )
                    elif elem_child.type == "qualified_type":
                        result.heritage.append(
                            (iface_name, "extends", elem_child.text.decode("utf-8"))
                        )
            # Fallback for older tree-sitter-go versions that may emit directly
            elif child.type == "type_identifier":
                result.heritage.append(
                    (iface_name, "extends", child.text.decode("utf-8"))
                )
            elif child.type == "qualified_type":
                result.heritage.append(
                    (iface_name, "extends", child.text.decode("utf-8"))
                )

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _extract_import(self, node: Node, result: ParseResult) -> None:
        """Handle both single and grouped import declarations."""
        for child in node.children:
            if child.type == "import_spec":
                self._extract_import_spec(child, result)
            elif child.type == "import_spec_list":
                for spec in child.children:
                    if spec.type == "import_spec":
                        self._extract_import_spec(spec, result)

    def _extract_import_spec(self, node: Node, result: ParseResult) -> None:
        path_node = node.child_by_field_name("path")
        if path_node is None:
            return

        module = self._string_value(path_node)
        if not module:
            return

        # Extract alias if present (e.g., ``myfmt "fmt"``, ``_ "pkg"``, ``. "pkg"``)
        name_node = node.child_by_field_name("name")
        alias = ""
        if name_node is not None:
            alias = name_node.text.decode("utf-8")

        # The imported name is the last segment of the path
        parts = module.rstrip("/").split("/")
        imported_name = parts[-1] if parts else module

        result.imports.append(
            ImportInfo(
                module=module,
                names=[imported_name],
                is_relative=False,
                alias=alias,
            )
        )

    # ------------------------------------------------------------------
    # Calls
    # ------------------------------------------------------------------

    def _extract_calls_recursive(self, node: Node, result: ParseResult) -> None:
        """Recursively find call expressions in a subtree."""
        if node.type == "call_expression":
            self._extract_call(node, result)

        for child in node.children:
            self._extract_calls_recursive(child, result)

    def _extract_call(self, node: Node, result: ParseResult) -> None:
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return

        line = node.start_point[0] + 1
        arguments = self._extract_identifier_arguments(node)

        if func_node.type == "selector_expression":
            operand = func_node.child_by_field_name("operand")
            field = func_node.child_by_field_name("field")
            if field is not None:
                receiver = self._root_identifier(operand) if operand else ""
                result.calls.append(
                    CallInfo(
                        name=field.text.decode("utf-8"),
                        line=line,
                        receiver=receiver,
                        arguments=arguments,
                    )
                )
        elif func_node.type == "identifier":
            result.calls.append(
                CallInfo(
                    name=func_node.text.decode("utf-8"),
                    line=line,
                    arguments=arguments,
                )
            )

    # ------------------------------------------------------------------
    # Type references from parameters and return types
    # ------------------------------------------------------------------

    def _extract_param_types(self, func_node: Node, result: ParseResult) -> None:
        """Extract type references from function/method parameters."""
        params = func_node.child_by_field_name("parameters")
        if params is None:
            return

        for child in params.children:
            if child.type == "parameter_declaration":
                type_node = child.child_by_field_name("type")
                name_node = child.child_by_field_name("name")
                if type_node is not None:
                    param_name = name_node.text.decode("utf-8") if name_node else ""
                    self._add_type_refs(
                        type_node, "param", type_node.start_point[0] + 1, result,
                        param_name=param_name,
                    )
            elif child.type == "variadic_parameter_declaration":
                # func f(args ...string) — the type is the last type_identifier child
                type_node = None
                name_node = None
                for sub in child.children:
                    if sub.type == "type_identifier":
                        type_node = sub
                    elif sub.type == "identifier":
                        name_node = sub
                if type_node is not None:
                    param_name = name_node.text.decode("utf-8") if name_node else ""
                    type_name = type_node.text.decode("utf-8")
                    if type_name and type_name not in _BUILTIN_TYPES:
                        result.type_refs.append(
                            TypeRef(
                                name=type_name,
                                kind="param",
                                line=type_node.start_point[0] + 1,
                                param_name=param_name,
                            )
                        )

    def _extract_return_types(self, func_node: Node, result: ParseResult) -> None:
        """Extract type references from return type."""
        ret = func_node.child_by_field_name("result")
        if ret is None:
            return

        # Single return type (identifier or pointer/slice/etc.)
        if ret.type in (
            "type_identifier", "pointer_type", "slice_type",
            "qualified_type", "generic_type", "map_type", "channel_type",
        ):
            self._add_type_refs(ret, "return", ret.start_point[0] + 1, result)
            return

        # Multiple return types in parameter_list
        if ret.type == "parameter_list":
            for child in ret.children:
                if child.type == "parameter_declaration":
                    type_node = child.child_by_field_name("type")
                    if type_node is not None:
                        self._add_type_refs(
                            type_node, "return", type_node.start_point[0] + 1, result
                        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _add_type_refs(
        self,
        type_node: Node,
        kind: str,
        line: int,
        result: ParseResult,
        param_name: str = "",
    ) -> None:
        """Extract all meaningful type references from a type node and add to result.

        Handles map types (both key and value), channel types, and all other
        type expressions via ``_unwrap_type_name``.
        """
        if type_node.type == "map_type":
            # For map[K]V, collect both key and value type refs
            # Children: map, [, key_type_identifier, ], value_type_identifier
            type_ids = [c for c in type_node.children if c.type == "type_identifier"]
            for tid in type_ids:
                name = tid.text.decode("utf-8")
                if name and name not in _BUILTIN_TYPES:
                    result.type_refs.append(
                        TypeRef(name=name, kind=kind, line=line, param_name=param_name)
                    )
            return

        if type_node.type == "channel_type":
            # chan T — find the element type
            for child in type_node.children:
                if child.type == "type_identifier":
                    name = child.text.decode("utf-8")
                    if name and name not in _BUILTIN_TYPES:
                        result.type_refs.append(
                            TypeRef(name=name, kind=kind, line=line, param_name=param_name)
                        )
            return

        type_name = self._unwrap_type_name(type_node)
        if type_name and type_name not in _BUILTIN_TYPES:
            result.type_refs.append(
                TypeRef(name=type_name, kind=kind, line=line, param_name=param_name)
            )

    @staticmethod
    def _unwrap_type_name(type_node: Node) -> str:
        """Extract a simple type name, unwrapping pointers, slices, etc.

        For ``map_type`` and ``channel_type`` use ``_add_type_refs`` instead,
        which can collect multiple names.  This method returns only the first
        meaningful name it finds.
        """
        if type_node.type == "type_identifier":
            return type_node.text.decode("utf-8")
        if type_node.type == "pointer_type":
            for child in type_node.children:
                if child.type == "type_identifier":
                    return child.text.decode("utf-8")
        if type_node.type == "slice_type":
            for child in type_node.children:
                if child.type == "type_identifier":
                    return child.text.decode("utf-8")
        if type_node.type == "qualified_type":
            # e.g., pkg.Type — return just the type name (last identifier)
            for child in reversed(type_node.children):
                if child.type == "type_identifier":
                    return child.text.decode("utf-8")
        if type_node.type == "generic_type":
            for child in type_node.children:
                if child.type == "type_identifier":
                    return child.text.decode("utf-8")
        if type_node.type == "map_type":
            # Return the value type (rightmost type_identifier)
            type_ids = [c for c in type_node.children if c.type == "type_identifier"]
            if len(type_ids) >= 2:
                return type_ids[-1].text.decode("utf-8")
            if type_ids:
                return type_ids[-1].text.decode("utf-8")
        if type_node.type == "channel_type":
            for child in type_node.children:
                if child.type == "type_identifier":
                    return child.text.decode("utf-8")
        # Fallback: find any type_identifier child
        for child in type_node.children:
            name = GoParser._unwrap_type_name(child)
            if name:
                return name
        return ""

    @staticmethod
    def _string_value(node: Node) -> str:
        """Extract raw string content from an interpreted or raw string literal."""
        # interpreted_string_literal: content is in interpreted_string_literal_content
        for child in node.children:
            if child.type == "interpreted_string_literal_content":
                return child.text.decode("utf-8")
            if child.type == "raw_string_literal_content":
                return child.text.decode("utf-8")
        # Fallback: strip quotes or backticks
        text = node.text.decode("utf-8")
        if len(text) >= 2:
            if text[0] == '"' and text[-1] == '"':
                return text[1:-1]
            if text[0] == "`" and text[-1] == "`":
                return text[1:-1]
        return text

    @staticmethod
    def _build_function_signature(node: Node, name: str) -> str:
        """Build a human-readable signature for a Go function/method."""
        params = node.child_by_field_name("parameters")
        result = node.child_by_field_name("result")
        receiver = node.child_by_field_name("receiver")

        sig = "func "
        if receiver is not None:
            sig += receiver.text.decode("utf-8") + " "
        sig += name
        if params is not None:
            sig += params.text.decode("utf-8")
        if result is not None:
            sig += " " + result.text.decode("utf-8")
        return sig

    @staticmethod
    def _extract_identifier_arguments(call_node: Node) -> list[str]:
        args = call_node.child_by_field_name("arguments")
        if args is None:
            return []
        return [
            child.text.decode("utf-8")
            for child in args.children
            if child.type == "identifier"
        ]

    @staticmethod
    def _root_identifier(node: Node) -> str:
        """Walk down to the leftmost identifier."""
        current = node
        while current is not None:
            if current.type == "identifier":
                return current.text.decode("utf-8")
            if current.children:
                current = current.children[0]
            else:
                break
        return ""
