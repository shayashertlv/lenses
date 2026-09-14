"""A shared, deliberately small Blender editing capability contract.

The AST check and execution builtins use the same imports. This is defense in
depth for generated editing code, not an operating-system security boundary.
The worker additionally isolates files, scrubs secrets and enforces stage locks.
"""
from __future__ import annotations

import ast
import builtins

MAX_SCRIPT_BYTES = 200_000
ALLOWED_IMPORTS = frozenset({
    "bpy", "bmesh", "math", "mathutils", "mathutils.bvhtree",
    "mathutils.kdtree", "mathutils.geometry",
})
ALLOWED_OPERATORS = frozenset({
    "bpy.ops.object.mode_set", "bpy.ops.object.select_all",
    "bpy.ops.object.modifier_apply", "bpy.ops.object.shade_smooth",
    "bpy.ops.mesh.select_all",
})
FORBIDDEN_ATTRIBUTES = frozenset({
    "app", "utils", "path", "libraries", "texts", "scripts", "preferences",
    "window_manager", "driver_add", "driver_remove", "animation_data_create",
    "load", "save", "save_render", "save_as_mainfile", "write", "read",
    "filepath", "filepath_raw", "filepath_from_user", "pack", "unpack",
    "reload", "user_remap", "as_pointer", "from_address", "from_pointer", "bl_rna", "rna_type", "bl_info",
    "register_class", "unregister_class", "filepaths", "blend_data",
    "callback_add", "callback_remove", "draw_handler_add", "draw_handler_remove",
})
FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "open", "input", "globals", "locals", "vars",
    "dir", "help", "breakpoint", "memoryview", "object", "type", "super",
    "classmethod", "staticmethod", "property", "delattr", "exit", "quit",
})


def _attribute_name(name: str) -> None:
    if not isinstance(name, str) or name.startswith("_") or name in FORBIDDEN_ATTRIBUTES or name == "ops":
        raise ValueError(f"Attribute {name!r} is outside the Blender editing contract")


def guarded_getattr(obj, name, *default):
    _attribute_name(name)
    return builtins.getattr(obj, name, *default)


def guarded_setattr(obj, name, value):
    _attribute_name(name)
    return builtins.setattr(obj, name, value)


def guarded_hasattr(obj, name):
    # Read-only protocol detection used when inspecting scalar vs array sockets.
    # This exposes a Boolean only, never the private method itself.
    if name != "__len__":
        _attribute_name(name)
    return builtins.hasattr(obj, name)


def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    fromlist = fromlist or ()
    if level or name not in ALLOWED_IMPORTS:
        raise ImportError(f"Import from {name!r} is unavailable to this Blender editor")
    if any(not isinstance(item, str) or item.startswith("_") or item == "*" for item in fromlist):
        raise ImportError("Private or wildcard imports are unavailable")
    # Python's import machinery may resolve a submodule from a from-import.
    if name in {"bpy", "bmesh"} and fromlist:
        raise ImportError(f"Use 'import {name}' and its documented data API")
    if name == "mathutils" and fromlist:
        allowed = {"Vector", "Matrix", "Quaternion", "Euler", "Color", "bvhtree", "kdtree", "geometry"}
        if not set(fromlist).issubset(allowed):
            raise ImportError("Unsupported mathutils export")
    return builtins.__import__(name, globals, locals, fromlist, level)


def safe_builtins(*, attribute_getter=None, attribute_setter=None) -> dict:
    names = (
        "abs all any bool bytearray bytes callable chr complex dict divmod enumerate filter float "
        "format frozenset hash hex int isinstance issubclass iter len list map max min "
        "next ord pow print range repr reversed round set slice sorted str sum tuple zip "
        "Exception RuntimeError ValueError TypeError KeyError IndexError AttributeError "
        "StopIteration ZeroDivisionError NotImplementedError AssertionError"
    ).split()
    result = {name: getattr(builtins, name) for name in names}
    result.update(__import__=guarded_import, getattr=guarded_getattr,
                  setattr=guarded_setattr, hasattr=guarded_hasattr)
    # Host adapters may change how an allowed attribute is accessed, never
    # which names are available to generated code.
    if attribute_getter is not None:
        def adapted_getattr(obj, name, *default):
            _attribute_name(name)
            return attribute_getter(obj, name, *default)
        result['getattr'] = adapted_getattr
    if attribute_setter is not None:
        def adapted_setattr(obj, name, value):
            _attribute_name(name)
            return attribute_setter(obj, name, value)
        result['setattr'] = adapted_setattr
    return result


def _chain(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _chain(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else None
    return None


def validate_script(script: str) -> None:
    if not isinstance(script, str) or not script.strip():
        raise ValueError("Astra returned an empty Python script")
    if len(script.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise ValueError("Python script exceeds the 200 KB editing limit")
    if "```" in script:
        raise ValueError("Return raw Python in the custom tool, without Markdown fences")
    try:
        tree = ast.parse(script, mode="exec")
    except (SyntaxError, RecursionError) as error:
        raise ValueError(f"Invalid Blender Python: {error}") from error
    nodes = list(ast.walk(tree))
    if len(nodes) > 35_000:
        raise ValueError("Python script is too complex for the bounded editor")
    aliases = {}
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
    def is_length_query(node):
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "hasattr" and len(node.args) == 2 and not node.keywords
                and isinstance(node.args[1], ast.Constant) and node.args[1].value == "__len__")
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.name not in ALLOWED_IMPORTS:
                        raise ValueError(f"Import from {item.name!r} is unavailable to the Blender editor")
                    aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
            else:
                if node.level or node.module not in ALLOWED_IMPORTS:
                    raise ValueError(f"Import from {node.module!r} is unavailable to the Blender editor")
                if node.module in {"bpy", "bmesh"}:
                    raise ValueError(f"Use 'import {node.module}' and its documented data API")
                for item in node.names:
                    if item.name.startswith("_") or item.name == "*":
                        raise ValueError("Private or wildcard imports are unavailable")
                    if node.module == "mathutils" and item.name not in {"Vector", "Matrix", "Quaternion", "Euler", "Color", "bvhtree", "kdtree", "geometry"}:
                        raise ValueError("Unsupported mathutils export")
    for node in nodes:
        if isinstance(node, ast.alias) and node.asname and node.asname.startswith('__'):
            raise ValueError("Private import aliases are unavailable")
        if isinstance(node, ast.ExceptHandler) and node.name and node.name.startswith('__'):
            raise ValueError("Private exception aliases are unavailable")
        if isinstance(node, (ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            name = node.rest if isinstance(node, ast.MatchMapping) else node.name
            if name and name.startswith('__'):
                raise ValueError("Private pattern aliases are unavailable")
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Await, ast.Global, ast.Nonlocal)):
            raise ValueError("Classes, async code and global declarations are unavailable")
        if isinstance(node, ast.Name) and (node.id.startswith("__") or node.id in FORBIDDEN_NAMES):
            raise ValueError(f"Name {node.id!r} is unavailable to the Blender editor")
        if isinstance(node, (ast.FunctionDef, ast.arg)):
            name = node.name if isinstance(node, ast.FunctionDef) else node.arg
            if name.startswith("__"):
                raise ValueError("Private names are unavailable")
        if isinstance(node, ast.Attribute):
            if node.attr == "as_pointer":
                # Blender's read-only datablock identity query. Allow only the
                # immediate zero-argument call, never capture/rebind the method
                # or retrieve it through the guarded attribute helpers.
                parent = parents.get(node)
                if not (isinstance(node.ctx, ast.Load) and isinstance(parent, ast.Call)
                        and parent.func is node and not parent.args and not parent.keywords):
                    raise ValueError("as_pointer is available only as a direct zero-argument identity query")
            elif node.attr == "ops":
                parent = parents.get(node)
                grandparent = parents.get(parent)
                call = parents.get(grandparent)
                full = _chain(grandparent, aliases) if grandparent else None
                if not (isinstance(parent, ast.Attribute) and isinstance(grandparent, ast.Attribute)
                        and isinstance(call, ast.Call) and call.func is grandparent and full in ALLOWED_OPERATORS):
                    # bmesh.ops is a documented in-memory geometry capability.
                    if _chain(node, aliases) != "bmesh.ops":
                        raise ValueError("Only explicitly documented Blender operators are available")
            elif node.attr.startswith("_") or node.attr in FORBIDDEN_ATTRIBUTES:
                raise ValueError(f"Attribute {node.attr!r} is unavailable to the Blender editor")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("__") or node.value in {"ShaderNodeScript", "CompositorNodeOutputFile"}:
                parent = parents.get(node)
                if not (node.value == "__len__" and is_length_query(parent) and parent.args[1] is node):
                    raise ValueError("Executable or external-file node capabilities are unavailable")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"getattr", "setattr", "hasattr"}:
            if len(node.args) < 2:
                raise ValueError("Attribute helpers require an explicit attribute")
            if isinstance(node.args[1], ast.Constant):
                if not is_length_query(node):
                    _attribute_name(node.args[1].value)
    # Syntax validation does not execute imports or query the installed Blender.
    # The worker probes the exact shared imports with its own native interpreter.
