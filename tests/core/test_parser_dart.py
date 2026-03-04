"""Tests for the Dart/Flutter parser."""

from pathlib import Path

import pytest

from axon.core.parsers.dart import DartParser

FIXTURE_DIR = Path(__file__).parents[2] / "test_projects" / "mix_lang"


def _parse(source: str):
    return DartParser().parse(source, "test.dart")


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


class TestDartClasses:
    def test_class_definition(self):
        result = _parse("class User {}")
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "User"

    def test_class_extends(self):
        result = _parse("class MyWidget extends StatelessWidget {}")
        assert ("MyWidget", "extends", "StatelessWidget") in result.heritage

    def test_class_implements(self):
        result = _parse("class Repo implements ApiService {}")
        assert ("Repo", "implements", "ApiService") in result.heritage

    def test_class_with_mixin(self):
        result = _parse(
            "class MyState extends State with TickerProviderStateMixin {}"
        )
        heritage = result.heritage
        extends_names = [h[2] for h in heritage if h[1] == "extends"]
        with_names = [h[2] for h in heritage if h[1] == "with"]
        assert "State" in extends_names
        assert "TickerProviderStateMixin" in with_names

    def test_class_with_multiple_mixins(self):
        result = _parse(
            "class MyState extends State with TickerProviderStateMixin, AutomaticKeepAliveClientMixin {}"
        )
        with_names = [h[2] for h in result.heritage if h[1] == "with"]
        assert "TickerProviderStateMixin" in with_names
        assert "AutomaticKeepAliveClientMixin" in with_names

    def test_no_duplicate_mixin_entries(self):
        result = _parse(
            "class MyState extends State with TickerProviderStateMixin {}"
        )
        mixin_entries = [h for h in result.heritage if h[2] == "TickerProviderStateMixin"]
        assert len(mixin_entries) == 1, "Mixin must not be duplicated"

    def test_abstract_class(self):
        result = _parse("abstract class Repo {}")
        kinds = {s.name: s.kind for s in result.symbols}
        assert kinds["Repo"] == "abstract_class"

    def test_generic_heritage(self):
        result = _parse("class MyState extends State<UserList> {}")
        extends_names = [h[2] for h in result.heritage if h[1] == "extends"]
        assert "State" in extends_names

    def test_class_implements_multiple(self):
        result = _parse("class Foo implements Bar, Baz {}")
        impl = [h[2] for h in result.heritage if h[1] == "implements"]
        assert "Bar" in impl
        assert "Baz" in impl

    def test_empty_file(self):
        result = _parse("")
        assert result.symbols == []
        assert result.imports == []
        assert result.calls == []

    def test_syntax_error_does_not_crash(self):
        # Should not raise, just produce partial results.
        result = _parse("class { broken dart ===")
        assert result is not None


# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------


class TestDartMethods:
    def test_method_in_class(self):
        result = _parse(
            "class W extends StatelessWidget {\n"
            "  Widget build(BuildContext context) {\n"
            "    return Container();\n"
            "  }\n"
            "}"
        )
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        assert methods[0].name == "build"
        assert methods[0].class_name == "W"

    def test_method_with_override_decorator(self):
        result = _parse(
            "class W extends StatelessWidget {\n"
            "  @override\n"
            "  Widget build(BuildContext context) {\n"
            "    return Container();\n"
            "  }\n"
            "}"
        )
        methods = [s for s in result.symbols if s.kind == "method"]
        assert methods[0].decorators == ["override"]

    def test_multiple_annotations(self):
        result = _parse(
            "class W {\n"
            "  @Deprecated('use newMethod')\n"
            "  @override\n"
            "  void oldMethod() {}\n"
            "}"
        )
        methods = [s for s in result.symbols if s.kind == "method"]
        assert any(m.name == "oldMethod" for m in methods)
        m = next(m for m in methods if m.name == "oldMethod")
        assert "override" in m.decorators

    def test_private_method(self):
        result = _parse("class W {\n  void _handleTap() {}\n}")
        methods = [s for s in result.symbols if s.kind == "method"]
        assert any(m.name == "_handleTap" for m in methods)

    def test_multiple_methods(self):
        result = _parse("class S {\n  void a() {}\n  void b() {}\n  void c() {}\n}")
        methods = [s for s in result.symbols if s.kind == "method"]
        names = {m.name for m in methods}
        assert names == {"a", "b", "c"}

    def test_async_method(self):
        result = _parse(
            "class S {\n"
            "  Future<void> loadData() async {\n"
            "    await fetchData();\n"
            "  }\n"
            "}"
        )
        methods = [s for s in result.symbols if s.kind == "method"]
        assert any(m.name == "loadData" for m in methods)

    def test_getter(self):
        result = _parse("class C {\n  int get count => _count;\n}")
        getters = [s for s in result.symbols if s.kind == "getter"]
        assert any(g.name == "count" for g in getters)

    def test_setter(self):
        result = _parse("class C {\n  set count(int v) { _count = v; }\n}")
        setters = [s for s in result.symbols if s.kind == "setter"]
        assert any(s.name == "count" for s in setters)

    def test_factory_constructor(self):
        result = _parse(
            "class MyClass {\n"
            "  factory MyClass.fromJson(Map json) => MyClass();\n"
            "}"
        )
        factories = [s for s in result.symbols if s.kind == "factory_constructor"]
        assert any("fromJson" in f.name for f in factories)

    def test_named_constructor(self):
        result = _parse(
            "class MyClass {\n"
            "  MyClass.empty() : this(0);\n"
            "}"
        )
        ctors = [s for s in result.symbols if s.kind == "constructor"]
        assert any("empty" in c.name for c in ctors)


# ---------------------------------------------------------------------------
# Functions (top-level)
# ---------------------------------------------------------------------------


class TestDartFunctions:
    def test_top_level_function(self):
        result = _parse('void main() {\n  print("hello");\n}')
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "main"

    def test_function_with_return_type(self):
        result = _parse("Widget buildApp() {\n  return Container();\n}")
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert funcs[0].name == "buildApp"


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


class TestDartImports:
    def test_package_import(self):
        result = _parse("import 'package:flutter/material.dart';")
        assert any(i.module == "package:flutter/material.dart" for i in result.imports)

    def test_dart_core_import(self):
        result = _parse("import 'dart:async';")
        assert result.imports[0].module == "dart:async"

    def test_multiple_imports(self):
        result = _parse(
            "import 'package:flutter/material.dart';\n"
            "import 'dart:async';\n"
            "import 'package:http/http.dart';\n"
        )
        assert len(result.imports) == 3

    def test_import_with_as_alias(self):
        result = _parse("import 'dart:ui' as ui;")
        imp = result.imports[0]
        assert imp.module == "dart:ui"
        assert imp.alias == "ui"

    def test_import_with_show(self):
        result = _parse(
            "import 'package:flutter/material.dart' show Widget, BuildContext;"
        )
        imp = result.imports[0]
        assert "Widget" in imp.names
        assert "BuildContext" in imp.names

    def test_import_with_hide(self):
        result = _parse(
            "import 'package:flutter/material.dart' hide Color;"
        )
        imp = result.imports[0]
        assert "Color" in imp.names


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestDartEnums:
    def test_enum(self):
        result = _parse("enum Color { red, green, blue }")
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) == 1
        assert enums[0].name == "Color"


# ---------------------------------------------------------------------------
# Type references
# ---------------------------------------------------------------------------


class TestDartTypeRefs:
    def test_param_type(self):
        result = _parse(
            "class W {\n"
            "  Widget build(BuildContext context) { return Container(); }\n"
            "}"
        )
        refs = [r for r in result.type_refs if r.kind == "param"]
        assert any(r.name == "BuildContext" for r in refs)

    def test_return_type(self):
        result = _parse(
            "class W {\n"
            "  Widget build(BuildContext context) { return Container(); }\n"
            "}"
        )
        refs = [r for r in result.type_refs if r.kind == "return"]
        assert any(r.name == "Widget" for r in refs)

    def test_field_type(self):
        result = _parse("class W {\n  final UserService service;\n}")
        refs = [r for r in result.type_refs if r.kind == "variable"]
        assert any(r.name == "UserService" for r in refs)


# ---------------------------------------------------------------------------
# Heritage
# ---------------------------------------------------------------------------


class TestDartHeritage:
    def test_stateful_widget_pattern(self):
        result = _parse(
            "class MyPage extends StatefulWidget {\n"
            "  @override\n"
            "  State<MyPage> createState() => _MyPageState();\n"
            "}\n"
        )
        assert ("MyPage", "extends", "StatefulWidget") in result.heritage
        methods = [s for s in result.symbols if s.kind == "method"]
        assert any(m.name == "createState" for m in methods)


# ---------------------------------------------------------------------------
# Call extraction
# ---------------------------------------------------------------------------


class TestDartCalls:
    def test_simple_call(self):
        result = _parse("void main() {\n  print('hello');\n}")
        names = [c.name for c in result.calls]
        assert "print" in names

    def test_method_call(self):
        result = _parse(
            "void f() {\n  widget.service.getUsers();\n}"
        )
        calls = result.calls
        assert any(c.name == "getUsers" for c in calls)
        getUsers = next(c for c in calls if c.name == "getUsers")
        assert "service" in getUsers.receiver or "widget" in getUsers.receiver

    def test_constructor_call(self):
        result = _parse("void main() {\n  runApp(MyApp());\n}")
        names = [c.name for c in result.calls]
        assert "runApp" in names
        assert "MyApp" in names

    def test_setState_call(self):
        result = _parse(
            "class S {\n"
            "  void f() {\n"
            "    setState(() { var x = 1; });\n"
            "  }\n"
            "}"
        )
        names = [c.name for c in result.calls]
        assert "setState" in names

    def test_chained_method_call(self):
        result = _parse(
            "void f() {\n"
            "  ListView.builder(itemCount: 5);\n"
            "}"
        )
        calls = result.calls
        assert any(c.name == "builder" for c in calls)
        builder = next(c for c in calls if c.name == "builder")
        assert "ListView" in builder.receiver

    def test_super_call(self):
        result = _parse(
            "class S {\n"
            "  void initState() {\n"
            "    super.initState();\n"
            "  }\n"
            "}"
        )
        names = [c.name for c in result.calls]
        assert "initState" in names

    def test_nested_call(self):
        # Text('hi') inside Container(child: ...)
        result = _parse(
            "void f() {\n"
            "  Container(child: Text('hi'));\n"
            "}"
        )
        names = [c.name for c in result.calls]
        assert "Container" in names
        assert "Text" in names

    def test_no_duplicate_calls_from_visited(self):
        # A single call should not appear multiple times due to double-walking.
        result = _parse("void main() {\n  print('hello');\n}")
        print_calls = [c for c in result.calls if c.name == "print"]
        assert len(print_calls) == 1


# ---------------------------------------------------------------------------
# Mixin declarations
# ---------------------------------------------------------------------------


class TestDartMixins:
    def test_mixin_declaration(self):
        result = _parse("mixin Loggable on Widget {\n  void log() {}\n}")
        mixins = [s for s in result.symbols if s.kind == "mixin"]
        assert any(m.name == "Loggable" for m in mixins)

    def test_mixin_on_clause(self):
        result = _parse("mixin Loggable on Widget {}")
        assert ("Loggable", "on", "Widget") in result.heritage

    def test_mixin_body_methods(self):
        result = _parse("mixin Loggable on Widget {\n  void log() {}\n}")
        methods = [s for s in result.symbols if s.kind == "method"]
        assert any(m.name == "log" and m.class_name == "Loggable" for m in methods)


# ---------------------------------------------------------------------------
# Extension declarations
# ---------------------------------------------------------------------------


class TestDartExtensions:
    def test_extension_declaration(self):
        result = _parse(
            "extension StringX on String {\n"
            "  bool get isBlank => trim().isEmpty;\n"
            "}"
        )
        exts = [s for s in result.symbols if s.kind == "extension"]
        assert any(e.name == "StringX" for e in exts)

    def test_extension_on_clause(self):
        result = _parse("extension StringX on String {}")
        assert ("StringX", "on", "String") in result.heritage

    def test_extension_getter(self):
        result = _parse(
            "extension StringX on String {\n"
            "  bool get isBlank => trim().isEmpty;\n"
            "}"
        )
        getters = [s for s in result.symbols if s.kind == "getter"]
        assert any(g.name == "isBlank" for g in getters)


# ---------------------------------------------------------------------------
# Typedef
# ---------------------------------------------------------------------------


class TestDartTypedef:
    def test_typedef(self):
        result = _parse("typedef Callback = void Function(int x);")
        typedefs = [s for s in result.symbols if s.kind == "typedef"]
        assert any(t.name == "Callback" for t in typedefs)


# ---------------------------------------------------------------------------
# Fixture file
# ---------------------------------------------------------------------------


class TestDartFixture:
    def test_fixture_parses(self):
        fixture = FIXTURE_DIR / "widget.dart"
        if not fixture.exists():
            pytest.skip("fixture not found")
        result = DartParser().parse(fixture.read_text(), str(fixture))

        # Classes
        class_names = {s.name for s in result.symbols if s.kind == "class"}
        assert "MyHomePage" in class_names
        assert "UserList" in class_names
        assert "_UserListState" in class_names

        # Heritage
        heritage_by_name = {(h[0], h[1]): h[2] for h in result.heritage}
        assert heritage_by_name.get(("MyHomePage", "extends")) == "StatelessWidget"
        assert heritage_by_name.get(("UserList", "extends")) == "StatefulWidget"

        # Methods
        method_names = {s.name for s in result.symbols if s.kind == "method"}
        assert "build" in method_names
        assert "initState" in method_names
        assert "_loadUsers" in method_names

        # Enums
        enum_names = {s.name for s in result.symbols if s.kind == "enum"}
        assert "UserRole" in enum_names

        # Imports
        modules = {i.module for i in result.imports}
        assert "package:flutter/material.dart" in modules
        assert "dart:async" in modules

        # Calls
        call_names = {c.name for c in result.calls}
        assert "runApp" in call_names
        assert "setState" in call_names
        assert "initState" in call_names

    def test_fixture_field_types(self):
        fixture = FIXTURE_DIR / "widget.dart"
        if not fixture.exists():
            pytest.skip("fixture not found")
        result = DartParser().parse(fixture.read_text(), str(fixture))

        type_names = {r.name for r in result.type_refs}
        assert "UserService" in type_names
