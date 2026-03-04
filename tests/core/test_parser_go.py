"""Tests for the Go parser."""

from axon.core.parsers.go import GoParser


def _parse(source: str):
    return GoParser().parse(source, "test.go")


class TestGoFunctions:
    def test_function_declaration(self):
        result = _parse('package main\n\nfunc Hello() {}')
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "Hello"

    def test_function_with_params_and_return(self):
        result = _parse(
            'package main\n\nfunc GetUser(id int) (*User, error) { return nil, nil }'
        )
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "GetUser"
        assert "GetUser" in funcs[0].signature

    def test_exported_functions_in_exports(self):
        result = _parse('package main\n\nfunc Exported() {}\nfunc private() {}')
        assert "Exported" in result.exports
        assert "private" not in result.exports

    def test_init_function_not_exported(self):
        """init() is a special non-exported function."""
        result = _parse('package main\n\nfunc init() {}')
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "init"
        assert "init" not in result.exports

    def test_anonymous_function_literal_calls_captured(self):
        """Calls inside anonymous functions should be captured."""
        src = 'package main\n\nfunc main() { f := func() { doStuff() }; f() }'
        result = _parse(src)
        names = [c.name for c in result.calls]
        assert "doStuff" in names

    def test_type_alias_func(self):
        """type Handler func(...) should be recorded as type_alias."""
        src = 'package main\n\ntype Handler func(w http.ResponseWriter, r *http.Request)'
        result = _parse(src)
        aliases = [s for s in result.symbols if s.kind == "type_alias"]
        assert any(a.name == "Handler" for a in aliases)

    def test_variadic_parameter_type_ref(self):
        """func f(args ...MyType) should produce a type_ref for MyType."""
        result = _parse('package main\n\nfunc Process(args ...MyType) {}')
        refs = [r for r in result.type_refs if r.kind == "param"]
        assert any(r.name == "MyType" for r in refs)

    def test_variadic_builtin_excluded(self):
        """func f(args ...string) should NOT produce a type_ref (string is builtin)."""
        result = _parse('package main\n\nfunc Process(args ...string) {}')
        refs = [r for r in result.type_refs if r.kind == "param"]
        assert len(refs) == 0

    def test_named_multi_return(self):
        """Named return values like (n int, err error) should be extracted."""
        result = _parse('package main\n\nfunc ReadAll() (n int, err error) { return }')
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert funcs[0].name == "ReadAll"
        # error is builtin but verifies parsing doesn't crash
        refs = [r for r in result.type_refs if r.kind == "return"]
        # int and error are both builtin — no non-builtin refs expected
        assert all(r.name not in ("int", "error") for r in refs)

    def test_named_multi_return_custom_type(self):
        """Named return with custom types should produce type_refs."""
        result = _parse('package main\n\nfunc New() (svc *Service, err error) { return }')
        refs = [r for r in result.type_refs if r.kind == "return"]
        assert any(r.name == "Service" for r in refs)


class TestGoMethods:
    def test_method_declaration(self):
        result = _parse(
            'package main\n\ntype S struct{}\n\nfunc (s *S) Do() {}'
        )
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        assert methods[0].name == "Do"
        assert methods[0].class_name == "S"

    def test_method_receiver_value_type(self):
        result = _parse(
            'package main\n\ntype S struct{}\n\nfunc (s S) Do() {}'
        )
        methods = [s for s in result.symbols if s.kind == "method"]
        assert methods[0].class_name == "S"


class TestGoTypes:
    def test_struct(self):
        result = _parse('package main\n\ntype User struct { Name string }')
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "User"

    def test_interface(self):
        result = _parse(
            'package main\n\ntype Reader interface { Read(p []byte) (int, error) }'
        )
        ifaces = [s for s in result.symbols if s.kind == "interface"]
        assert len(ifaces) == 1
        assert ifaces[0].name == "Reader"

    def test_type_alias(self):
        result = _parse('package main\n\ntype ID string')
        aliases = [s for s in result.symbols if s.kind == "type_alias"]
        assert len(aliases) == 1
        assert aliases[0].name == "ID"

    def test_exported_type(self):
        result = _parse('package main\n\ntype Public struct{}\ntype private struct{}')
        assert "Public" in result.exports
        assert "private" not in result.exports


class TestGoImports:
    def test_single_import(self):
        result = _parse('package main\n\nimport "fmt"')
        assert len(result.imports) == 1
        assert result.imports[0].module == "fmt"
        assert result.imports[0].names == ["fmt"]

    def test_grouped_imports(self):
        result = _parse('package main\n\nimport (\n\t"fmt"\n\t"net/http"\n)')
        assert len(result.imports) == 2
        modules = {i.module for i in result.imports}
        assert "fmt" in modules
        assert "net/http" in modules
        http_imp = [i for i in result.imports if i.module == "net/http"][0]
        assert http_imp.names == ["http"]

    def test_aliased_import(self):
        result = _parse('package main\n\nimport myfmt "fmt"')
        assert result.imports[0].alias == "myfmt"

    def test_blank_import(self):
        """import _ "pkg" — side-effect import, should be recorded."""
        result = _parse('package main\n\nimport _ "database/sql"')
        assert len(result.imports) == 1
        assert result.imports[0].module == "database/sql"
        assert result.imports[0].alias == "_"

    def test_dot_import(self):
        """import . "pkg" — dot import, should be recorded."""
        result = _parse('package main\n\nimport . "math"')
        assert len(result.imports) == 1
        assert result.imports[0].module == "math"
        assert result.imports[0].alias == "."

    def test_raw_string_literal_import(self):
        """import paths can be raw string literals (backticks)."""
        result = _parse('package main\n\nimport `github.com/foo/bar`')
        assert len(result.imports) == 1
        assert result.imports[0].module == "github.com/foo/bar"

    def test_blank_and_dot_in_grouped_import(self):
        src = (
            'package main\n\nimport (\n'
            '    _ "database/sql"\n'
            '    . "math"\n'
            '    "fmt"\n'
            ')'
        )
        result = _parse(src)
        assert len(result.imports) == 3
        modules = {i.module for i in result.imports}
        assert "database/sql" in modules
        assert "math" in modules
        assert "fmt" in modules
        blank = next(i for i in result.imports if i.module == "database/sql")
        dot = next(i for i in result.imports if i.module == "math")
        assert blank.alias == "_"
        assert dot.alias == "."


class TestGoCalls:
    def test_simple_call(self):
        result = _parse('package main\n\nfunc main() { doStuff() }')
        names = [c.name for c in result.calls]
        assert "doStuff" in names

    def test_method_call(self):
        result = _parse('package main\n\nfunc main() { svc.GetUser(1) }')
        calls = [c for c in result.calls if c.name == "GetUser"]
        assert len(calls) == 1
        assert calls[0].receiver == "svc"

    def test_package_call(self):
        result = _parse('package main\n\nimport "fmt"\n\nfunc main() { fmt.Println("hi") }')
        calls = [c for c in result.calls if c.name == "Println"]
        assert len(calls) == 1
        assert calls[0].receiver == "fmt"

    def test_goroutine_call_captured(self):
        """go fn() — the call inside go statement should be captured."""
        result = _parse('package main\n\nfunc main() { go worker() }')
        names = [c.name for c in result.calls]
        assert "worker" in names

    def test_defer_call_captured(self):
        """defer fn() — the call inside defer statement should be captured."""
        result = _parse('package main\n\nfunc main() { defer cleanup() }')
        names = [c.name for c in result.calls]
        assert "cleanup" in names

    def test_goroutine_method_call_captured(self):
        result = _parse('package main\n\nfunc main() { go svc.Process() }')
        calls = [c for c in result.calls if c.name == "Process"]
        assert len(calls) == 1
        assert calls[0].receiver == "svc"


class TestGoTypeRefs:
    def test_param_type(self):
        result = _parse('package main\n\nfunc Do(u *User) {}')
        refs = [r for r in result.type_refs if r.kind == "param"]
        assert any(r.name == "User" for r in refs)

    def test_return_type(self):
        result = _parse('package main\n\nfunc Do() *User { return nil }')
        refs = [r for r in result.type_refs if r.kind == "return"]
        assert any(r.name == "User" for r in refs)

    def test_struct_field_type(self):
        result = _parse('package main\n\ntype S struct { db *Database }')
        refs = [r for r in result.type_refs if r.kind == "variable"]
        assert any(r.name == "Database" for r in refs)

    def test_builtin_types_excluded(self):
        result = _parse('package main\n\nfunc Do(s string, i int) {}')
        refs = [r for r in result.type_refs if r.kind == "param"]
        assert len(refs) == 0

    def test_map_param_both_key_and_value(self):
        """map[string]User — string is builtin, User should appear as type_ref."""
        result = _parse('package main\n\nfunc Do(m map[string]User) {}')
        ref_names = {r.name for r in result.type_refs if r.kind == "param"}
        assert "User" in ref_names
        assert "string" not in ref_names  # builtin excluded

    def test_map_both_custom_types(self):
        """map[Key]Value — both should appear as type_refs."""
        result = _parse('package main\n\nfunc Do(m map[Key]Value) {}')
        ref_names = {r.name for r in result.type_refs if r.kind == "param"}
        assert "Key" in ref_names
        assert "Value" in ref_names

    def test_channel_param_type(self):
        """chan MyType — MyType should appear as type_ref."""
        result = _parse('package main\n\nfunc Do(ch chan MyEvent) {}')
        ref_names = {r.name for r in result.type_refs if r.kind == "param"}
        assert "MyEvent" in ref_names

    def test_channel_builtin_excluded(self):
        result = _parse('package main\n\nfunc Do(ch chan int) {}')
        refs = [r for r in result.type_refs if r.kind == "param"]
        assert len(refs) == 0


class TestGoInterfaceEmbedding:
    def test_interface_embedding(self):
        """type ReadWriter interface { Reader; Writer } — heritage recorded."""
        src = (
            'package main\n\n'
            'type ReadWriter interface {\n'
            '    Reader\n'
            '    Writer\n'
            '}'
        )
        result = _parse(src)
        assert ("ReadWriter", "extends", "Reader") in result.heritage
        assert ("ReadWriter", "extends", "Writer") in result.heritage

    def test_single_interface_embedding(self):
        src = 'package main\n\ntype WriterCloser interface { Writer }'
        result = _parse(src)
        assert ("WriterCloser", "extends", "Writer") in result.heritage

    def test_empty_interface_no_heritage(self):
        src = 'package main\n\ntype Empty interface{}'
        result = _parse(src)
        assert result.heritage == []

    def test_interface_with_methods_and_embedding(self):
        """Ensure methods in the interface don't cause false heritage entries."""
        src = (
            'package main\n\n'
            'type ReadWriter interface {\n'
            '    Reader\n'
            '    Write(p []byte) (int, error)\n'
            '}'
        )
        result = _parse(src)
        assert ("ReadWriter", "extends", "Reader") in result.heritage
        # Write is a method, not an embedded type
        names = [h[2] for h in result.heritage]
        assert "Write" not in names


class TestGoStructEmbedding:
    def test_struct_embedding(self):
        """Embedded struct fields produce heritage entries."""
        src = (
            'package main\n\n'
            'type Admin struct {\n'
            '    User\n'
            '    *Permission\n'
            '}'
        )
        result = _parse(src)
        assert ("Admin", "extends", "User") in result.heritage
        assert ("Admin", "extends", "Permission") in result.heritage

    def test_struct_embedding_only_anonymous_fields(self):
        """Named fields should NOT produce heritage entries."""
        src = (
            'package main\n\n'
            'type Admin struct {\n'
            '    Name string\n'
            '    User\n'
            '}'
        )
        result = _parse(src)
        assert ("Admin", "extends", "User") in result.heritage
        # Named field "Name" must not appear in heritage
        assert len(result.heritage) == 1


class TestGoEdgeCases:
    def test_empty_file(self):
        """Empty source should not crash."""
        result = _parse("")
        assert result.symbols == []
        assert result.imports == []
        assert result.calls == []

    def test_package_only(self):
        """Only a package declaration — no symbols."""
        result = _parse("package main\n")
        assert result.symbols == []

    def test_generic_function(self):
        """Generic functions [T any] should parse without error."""
        src = 'package main\n\nfunc Map[T any](s []T) []T { return s }'
        result = _parse(src)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any(f.name == "Map" for f in funcs)

    def test_multiple_functions(self):
        src = (
            'package main\n\n'
            'func A() {}\n'
            'func B() {}\n'
            'func C() {}\n'
        )
        result = _parse(src)
        names = [s.name for s in result.symbols if s.kind == "function"]
        assert set(names) == {"A", "B", "C"}
