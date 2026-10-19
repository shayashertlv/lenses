"""Safe mode for Astra's `execute_blender_code` (glasses3d/plan/archive/20261019/WP2_DESIGN.md §4). Stdlib only: the host validates a snippet before a byte
crosses the socket, and the Blender worker validates it again and then runs it under the runtime layers below.

Validator mechanics
-------------------
Adapted from the community MCP's safe mode (automation/data/blender_agent/mcp-for-blender/src/blender_mcp/safe_mode.py
:477-975: `_attr_chain`, `_path_is_blocked`, `_collect_bindings`, the `_Validator` node walk, `_check_module_value_use`,
`_guard_source`, `_check_depth`), under its licence:

    MIT License. Copyright (c) 2025 Siddharth Ahuja.
    Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
    documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
    rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
    persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice
    and this permission notice shall be included in all copies or substantial portions of the Software.
    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
    WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
    COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
    OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Changed here (glasses3d/plan/archive/20261019/WP2_DESIGN.md §4): every violation is collected (Astra fixes them all at once) with its line and the tool to
use instead; `import numpy as np` style aliases are canonicalised (bpy aliases stay denied; a name may alias one module
only); `np`, `bpy`, `bmesh`, `mathutils` and the `g3d` facade are prebound; a lambda is allowed as a value and never as a
call target; every `_`-prefixed attribute is denied; from-import names are checked against the denied names too (closes
`from numpy import load`); `.format` only on a literal whose field segments pass the attribute rules; getattr/setattr/hasattr/delattr
with a COMPUTED name pass the static walk and are checked at run time; any module object used as a value is refused
(an attribute chain must start at the module name); module attributes that lead out of the allowlist (`typing.sys`,
`enum.bltns`, `json.codecs`, `bpy_extras.object_utils.bpy`, measured in Blender 5.2) are denied; limits 200 KB, 40,000
AST nodes, depth 32.

Policy (glasses3d, glasses3d/plan/archive/20261019/PORT_REVIEW.md §1): numpy allowed; file I/O, pickle, ctypes, subprocess, os/sys escapes, bpy file
operators and `.filepath` assignment denied. Trusted host calls (checkpoint, export, render) never pass through here.

Runtime layers (the static walk is not a security boundary): restricted builtins; a guarded `__import__` and wrapped
get/set/has/delattr (`RuntimeGuard`); an audit hook armed only while Astra's code runs on the main thread (`AuditGuard`);
the worker adds a post-call active-file / save-handler / persistence check and the secret-free environment.
"""
from __future__ import annotations

import ast
import builtins as _py_builtins
import os
import string
import sys
import threading
import types
from dataclasses import dataclass

from .lib import NAMES as _FACADE_NAMES

MAX_CODE_BYTES = 200_000
MAX_AST_NODES = 40_000
MAX_NESTING_DEPTH = 32


@dataclass(frozen=True)
class Violation:
    line: int | None
    message: str
    use_instead: str | None = None

    def as_dict(self) -> dict:
        return {"line": self.line, "message": self.message, "use_instead": self.use_instead}

    def __str__(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        return where + self.message + (f" Use instead: {self.use_instead}" if self.use_instead else "")


# ------------------------------------------------------------------------------------------------ refusal hints
HINTS = {
    "checkpoint": "nothing: checkpoints are automatic after every call that changes the scene; "
                  "save_checkpoint(label) labels one and restore_checkpoint(id) goes back",
    "render": "render_views(...) for inspection renders (custom cameras allowed) and compare_to_photos(...) to compare "
              "with the photos; output paths are host-owned",
    "export": "the export_glb tool (validates the spec too); use the finish tool to complete the run",
    "font": "the load_font tool with family arial, arialbd, times, timesbd, calibri or segoeui; "
            "then assign bpy.data.fonts[result.name] to the text curve's font in execute_blender_code",
    "text": "scene custom properties for notes, e.g. bpy.context.scene['notes'] = '...'",
    "image_save": "nothing: images you generate or edit are packed into the next checkpoint automatically",
    "image_load": "bpy.data.images.new(...) and fill its pixels; image files are host tools",
    "import_file": "build the geometry in code; files are never imported by scene code",
    "numpy_io": "keep arrays in memory; return small results in `result` or store them in custom properties",
    "preferences": "nothing: preferences are not scene state (export settings are fixed by export_glb())",
    "undo": "restore_checkpoint(id)",
    "file_path": "nothing: file paths are host-controlled",
    "g3d": "the g3d facade (" + ", ".join(f"g3d.{name}" for name in _FACADE_NAMES) + "); "
           "use the named seating tools for seat_lens, hide_eyewire, check_lens_seating, unseat_lens, unhide_eyewire",
}

# ------------------------------------------------------------------------------------------------ module policy
ALLOWED_MODULES = frozenset({
    "bpy", "bmesh", "mathutils", "numpy", "math", "cmath", "random", "colorsys", "json", "itertools", "functools",
    "collections", "statistics", "string", "re", "enum", "dataclasses", "typing", "decimal", "fractions", "textwrap",
    "unicodedata", "copy", "heapq", "bisect", "array", "time", "bpy_extras", "g3d"})
ALLOWED_SUBMODULES = frozenset({
    "bmesh.ops", "bmesh.types", "bmesh.utils", "bmesh.geometry",
    "mathutils.geometry", "mathutils.bvhtree", "mathutils.kdtree", "mathutils.noise", "mathutils.interpolate",
    "numpy.linalg", "numpy.random", "numpy.fft", "numpy.ma", "numpy.lib.stride_tricks",
    "numpy.polynomial", "numpy.polynomial.polynomial", "numpy.polynomial.chebyshev", "numpy.polynomial.legendre",
    "numpy.polynomial.hermite", "numpy.polynomial.hermite_e", "numpy.polynomial.laguerre",
    "numpy.polynomial.polyutils",
    "collections.abc", "bpy_extras.object_utils", "bpy_extras.mesh_utils", "bpy_extras.view3d_utils", "g3d.lib"})
MODULE_PATHS = ALLOWED_MODULES | ALLOWED_SUBMODULES
#: names prebound in every snippet's namespace (local name -> module path)
PREBOUND_MODULES = {"bpy": "bpy", "bmesh": "bmesh", "mathutils": "mathutils", "np": "numpy", "g3d": "g3d"}
PREBOUND_NAMES = frozenset({"result"})
#: Share the explicit facade registry with its builder. Importing lib only
#: loads stdlib types; Blender implementations load later, inside lib.build().
#: Both AST paths/from-imports and runtime imports/getattr use this same set.
G3D_LIB_NAMES = frozenset(_FACADE_NAMES)
#: module attributes that lead outside the allowlist, measured in Blender 5.2 (a `--probe` worker recomputes them and
#: reports any edge missing here as `safe_mode_selfcheck.unlisted_edges` in its receipt; test_wp2_worker asserts none)
ESCAPE_EDGES = {
    "numpy": frozenset({"char", "core", "ctypeslib", "dtypes", "emath", "exceptions", "f2py", "lib", "rec", "strings",
                        "testing", "typing"}),
    "numpy.linalg": frozenset({"linalg"}), "numpy.random": frozenset({"bit_generator", "mtrand"}),
    "numpy.fft": frozenset({"helper"}), "numpy.polynomial.polyutils": frozenset({"operator", "warnings"}),
    "numpy.ma": frozenset({"core", "extras"}), "json": frozenset({"codecs", "decoder", "encoder", "scanner"}),
    "collections.abc": frozenset({"sys"}), "statistics": frozenset({"numbers", "sys"}), "re": frozenset({"copyreg"}),
    "enum": frozenset({"bltns", "sys"}), "dataclasses": frozenset({"abc", "inspect", "keyword", "sys", "types"}),
    "typing": frozenset({"copyreg", "operator", "sys", "types"}),
    "fractions": frozenset({"numbers", "operator", "sys"}),
    "bpy_extras": frozenset({"asset_utils", "io_utils", "node_utils"}), "bpy_extras.object_utils": frozenset({"bpy"}),
    # numpy re-rooted under short names inside every polynomial family module (numpy.linalg as `la`, polyutils as `pu`)
    **{f"numpy.polynomial.{fam}": frozenset({"la", "pu"})
       for fam in ("polynomial", "chebyshev", "legendre", "hermite", "hermite_e", "laguerre")},
}
#: attribute names that name a process/filesystem/introspection module: denied anywhere in a chain
DANGEROUS_SEGMENTS = frozenset({
    "sys", "os", "builtins", "bltns", "subprocess", "socket", "ctypes", "codecs", "io", "shutil", "pathlib",
    "importlib", "pickle", "marshal", "inspect", "copyreg", "posix", "nt", "msvcrt", "winreg", "threading",
    "multiprocessing", "signal", "tempfile", "glob", "urllib", "http", "asyncio", "runpy", "pkgutil", "gc", "linecache",
    "tokenize", "logging", "webbrowser", "platform", "sysconfig", "site", "zipimport", "mmap", "ssl", "sqlite3",
    "zipfile", "tarfile", "shelve", "operator", "io_utils", "asset_utils", "node_utils", "warnings", "abc", "numbers",
    "keyword"})
#: a module re-rooted under another path would blind the path rules (bpy_extras.object_utils.bpy.ops...)
REROOT_SEGMENTS = frozenset({"bpy", "numpy", "np", "bmesh", "mathutils", "g3d"})

DENIED_IMPORT_NAMES = frozenset({
    "system", "popen", "SystemRandom", "previews", "path", "environ", "exit", "argv", "modules", "builtins",
    "__builtins__", "__import__", "__loader__", "__spec__", "reload", "import_module", "find_spec", "util"})

# ------------------------------------------------------------------------------------------------ builtin policy
ALLOWED_BUILTINS = frozenset({
    "bool", "int", "float", "complex", "str", "bytes", "bytearray", "list", "tuple", "set", "frozenset", "dict",
    "slice", "abs", "round", "min", "max", "sum", "pow", "divmod", "hex", "oct", "bin", "ord", "chr", "len", "range",
    "enumerate", "zip", "map", "filter", "reversed", "sorted", "all", "any", "iter", "next", "isinstance",
    "issubclass", "callable", "repr", "format", "hash", "id", "print", "type", "Exception", "ValueError", "TypeError",
    "KeyError", "IndexError", "RuntimeError", "AttributeError", "ZeroDivisionError", "StopIteration",
    "NotImplementedError", "ArithmeticError", "OverflowError", "LookupError", "AssertionError", "FloatingPointError",
    "True", "False", "None", "NotImplemented", "Ellipsis"})
ATTR_BUILTINS = frozenset({"getattr", "setattr", "delattr", "hasattr"})      # wrapped at run time
FORBIDDEN_CALLABLES = {
    "eval": "eval() executes arbitrary expressions", "exec": "exec() executes arbitrary code",
    "compile": "compile() produces executable code objects", "__import__": "__import__() bypasses the import allowlist",
    "open": "open() is raw file access", "input": "input() blocks Blender on stdin",
    "breakpoint": "breakpoint() opens a debugger", "exit": "exit() terminates Blender",
    "quit": "quit() terminates Blender", "globals": "globals() exposes the namespace",
    "locals": "locals() exposes the namespace", "vars": "vars() exposes __dict__",
    "dir": "dir() enumerates attributes for dynamic lookups", "help": "help() imports modules",
    "memoryview": "memoryview() is raw buffer access", "super": "super() reaches base classes",
    "object": "object() is the root of the subclass ladder", "staticmethod": "descriptors are not needed",
    "classmethod": "descriptors are not needed", "property": "descriptors are not needed",
    "copyright": "site builtins", "credits": "site builtins", "license": "site builtins"}

# ------------------------------------------------------------------------------------------------ attribute policy
FORBIDDEN_ATTRS = frozenset({
    "mro", "func_globals", "func_code", "func_closure", "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code",
    "cr_await", "ag_frame", "ag_code", "f_globals", "f_locals", "f_builtins", "f_back", "f_code", "f_trace",
    "tb_frame", "tb_next", "co_code", "co_consts", "co_names",
    # attribute-traversal gadgets: string.Formatter walks `{0.__class__}` field names with the real getattr; typing
    # evaluates annotation strings with eval (an explicit empty globals dict brings the real builtins back)
    "format_map", "vformat", "get_field", "get_value", "get_type_hints", "ForwardRef", "evaluate_forward_ref",
    # raw memory: arbitrary strides read and write outside the array
    "as_strided", "path_resolve"})

#: denied wherever they appear in a chain, as a bare attribute and as a from-import name: (reason, hint key)
DENIED_NAMES = {
    # numpy file I/O, pickles, raw memory, native code
    "load": ("loads a file (numpy.load unpickles; datablock .load reads a file)", "numpy_io"),
    "save": ("writes a file (numpy.save / image.save)", "numpy_io"),
    "savez": ("writes a file", "numpy_io"), "savez_compressed": ("writes a file", "numpy_io"),
    "savetxt": ("writes a file", "numpy_io"), "loadtxt": ("reads a file", "numpy_io"),
    "genfromtxt": ("reads a file", "numpy_io"), "fromfile": ("reads a file", "numpy_io"),
    "tofile": ("writes a file", "numpy_io"), "memmap": ("maps a file into memory", "numpy_io"),
    "open_memmap": ("maps a file into memory", "numpy_io"), "fromregex": ("reads a file", "numpy_io"),
    "DataSource": ("opens files and URLs", "numpy_io"), "dump": ("pickles to a file", "numpy_io"),
    "ctypes": ("raw memory pointers", None), "ctypeslib": ("raw memory and native libraries", None),
    "f2py": ("compiles and runs native code", None), "testing": ("numpy.testing spawns processes", None),
    "distutils": ("build tooling", None), "show_config": ("imports build internals", None),
    "show_runtime": ("imports internals", None), "info": ("imports arbitrary modules", None),
    "get_include": ("filesystem paths", None), "lib": ("numpy.lib holds the file readers (only "
                                                       "numpy.lib.stride_tricks is allowed)", "numpy_io"),
    # bpy file I/O
    "save_render": ("writes an image file", "image_save"), "save_as": ("writes a file", "image_save"),
    "unpack": ("writes packed data to disk", "image_save"), "reload": ("re-reads a file from disk", "image_load"),
    "pack": ("packing reads files into the .blend", "image_save"),
    "save_mainfile": ("writes a .blend file", "checkpoint"), "save_as_mainfile": ("writes a .blend file", "checkpoint"),
    "open_mainfile": ("replaces the session with a .blend file", "checkpoint"),
    "preferences": ("user preferences are not scene state (temp paths, add-ons, autoexec)", "preferences"),
    "environ": ("the process environment", None), "clipboard": ("the owner's system clipboard", None),
    # persistence and code execution (community list)
    "driver_namespace": ("injects globals into driver evaluation", None),
    "register_class": ("class registration persists code", None),
    "unregister_class": ("class registration persists code", None), "execfile": ("executes a file", None),
    "load_scripts": ("executes scripts from disk", None), "save_homefile": ("overwrites the startup file", None),
    "quit_blender": ("terminates Blender", None), "url_open": ("opens a URL", None),
    "path_open": ("opens a path with the OS", None), "console_toggle": ("opens a console", None),
    "as_pointer": ("leaks a raw memory address", None), "driver_add": ("drivers evaluate Python expressions", None),
    "driver_remove": ("part of the driver surface", None), "texts": ("text datablocks are an execution path", "text"),
    "scripts": ("script datablocks are an execution path", None),
    "libraries": ("links external .blend files, which can carry code", "import_file"),
    "handlers": ("handler registration persists code", None), "timers": ("timers persist code", None),
    "app_handlers": ("handler registration persists code", None), "binary_path": ("the Blender executable", None),
    "user_resource": ("writable disk paths", None), "script_paths": ("script search paths", None),
    "script_directories": ("script search paths", None), "modules_from_path": ("imports modules from disk", None),
    "python_file_run": ("executes a file", None), "run_script": ("executes a text datablock", None),
    "addon_install": ("installs an add-on", None), "addon_enable": ("executes add-on code", None),
    "draw_handler_add": ("registers a persistent callback", None),
    "draw_cursor_add": ("registers a persistent callback", None),
    "event_timer_add": ("registers a persistent callback", None),
    "modal_handler_add": ("registers a persistent callback", None),
}
#: assigning any of these on any object
ASSIGN_DENIED = {
    "filepath": ("file paths are host-controlled", "file_path"), "filepath_raw": ("file paths are host-controlled",
                                                                                  "file_path"),
    "directory": ("file paths are host-controlled", "file_path"), "filename": ("file paths are host-controlled",
                                                                               "file_path"),
    "expression": ("driver expressions run Python", None), "script": ("script nodes execute their datablock", None),
    "use_self": ("driver access to the owning datablock", None), "script_directory": ("script search path", None),
    "use_autopack": ("file packing policy is host-owned", "image_save"),
    "use_scripts_auto_execute": ("enables embedded scripts", None)}
#: keyword arguments refused on every call (file paths, and bake's external save mode)
KWARGS_DENIED = {"filepath": "file_path", "filepath_raw": "file_path", "filename": "file_path",
                 "directory": "file_path", "files": "file_path", "relative_path": "file_path",
                 "save_mode": "image_save"}

# ------------------------------------------------------------------------------------------------ bpy path policy
BPY_PATHS_DENIED = (
    ("bpy.app.driver_namespace", "injects globals into driver evaluation", None),
    ("bpy.app.handlers", "handler registration persists code", None),
    ("bpy.app.timers", "timers persist code", None),
    ("bpy.app.binary_path", "the Blender executable", None),
    ("bpy.app.tempdir", "a filesystem path", "file_path"),
    ("bpy.utils", "registration and disk paths", None),
    ("bpy.data.texts", "text datablocks are an execution path", "text"),
    ("bpy.data.libraries", "external .blend files can carry code", "import_file"),
    ("bpy.data.scripts", "script datablocks are an execution path", None),
    ("bpy.props", "property registration persists definitions", None),
    ("bpy.msgbus", "message-bus subscriptions persist callbacks", None),
    ("bpy.context.preferences", "user preferences are not scene state", "preferences"),
    ("bpy.path", "filesystem paths", "file_path"),
    ("bpy.types.Operator", "defining operators registers code", None),
    ("bpy.types.Panel", "defining panels registers code", None),
    ("bpy.types.AddonPreferences", "add-on preferences persist", None),
    ("bpy.types.Macro", "macros chain operators", None),
)
#: whole bpy.ops namespaces: (reason, hint)
OPS_NAMESPACES_DENIED = {
    "wm": ("file open/save/append/link/import/export are host tools", "checkpoint"),
    "render": ("rendering is the render_views tool", "render"), "image": ("image files are host tools", "image_save"),
    "file": ("packing, unpacking and paths are host operations", "image_save"),
    "sound": ("file I/O", None), "clip": ("file I/O", None), "sequencer": ("file I/O", None),
    "cachefile": ("file I/O", None), "screen": ("UI and render-window operators", "render"),
    "buttons": ("the file browser", None), "text": ("runs text datablocks", "text"),
    "script": ("executes Python", None), "preferences": ("installs and enables add-ons", "preferences"),
    "extensions": ("installs packages", None), "console": ("executes Python", None),
    "ed": ("undo history is replaced by checkpoints", "undo"), "ptcache": ("writes disk caches", None),
    "fluid": ("simulation bakes write disk caches", None), "dpaint": ("bakes write image sequences", None)}
OPS_DENIED = {
    "font.open": ("loads a font file", "font"), "node.add_file": ("loads a file", "image_load"),
    "object.bake_image": ("writes image files", "image_save"),
    "outliner.lib_operation": ("library loading", "import_file"),
    "object.simulation_nodes_cache_bake": ("writes a disk cache", None),
    "object.geometry_node_bake_single": ("writes bake files to disk", None),
    "object.geometry_node_bake_pack_single": ("reads bake files from disk", None),
    "object.geometry_node_bake_unpack_single": ("writes bake files to disk", None)}
#: attribute names that exist to navigate toward the blocked surface (an unresolvable receiver may not reach them)
MODULE_NAVIGATION = frozenset({"ops", "utils", "app", "props", "types", "wm", "script", "preferences", "extensions",
                               "msgbus"})

# ------------------------------------------------------------------------------------------------ node policy
ALLOWED_NODES = frozenset({
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete, ast.Pass, ast.Break, ast.Continue,
    ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler, ast.Raise, ast.Assert, ast.With, ast.withitem,
    ast.FunctionDef, ast.Return, ast.arguments, ast.arg, ast.keyword, ast.Call, ast.Attribute, ast.Name, ast.Load,
    ast.Store, ast.Del, ast.Constant, ast.JoinedStr, ast.FormattedValue, ast.List, ast.Tuple, ast.Set, ast.Dict,
    ast.Subscript, ast.Slice, ast.Starred, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.ListComp,
    ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension, ast.Lambda,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.LShift, ast.RShift, ast.BitOr, ast.BitXor,
    ast.BitAnd, ast.MatMult, ast.And, ast.Or, ast.Not, ast.UAdd, ast.USub, ast.Invert, ast.Eq, ast.NotEq, ast.Lt,
    ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn}
    | ({ast.TryStar} if hasattr(ast, "TryStar") else set()))
NODE_EXPLANATIONS = {
    "ClassDef": "class definitions are the registration path for persistent bpy types",
    "Global": "global rebinds module-scope names", "Nonlocal": "nonlocal rebinds enclosing-scope names",
    "AsyncFunctionDef": "async has no event loop here", "AsyncFor": "async has no event loop here",
    "AsyncWith": "async has no event loop here", "Await": "async has no event loop here",
    "Yield": "generators defer execution past the call", "YieldFrom": "generators defer execution past the call",
    "NamedExpr": "walrus assignment obscures call-target analysis",
    "Match": "structural pattern matching binds names implicitly", "TypeAlias": "type aliases are not needed",
    "TypeVar": "type parameters are not needed", "ParamSpec": "type parameters are not needed",
    "TypeVarTuple": "type parameters are not needed"}


# ------------------------------------------------------------------------------------------------ path rules
def _attr_chain(node: ast.AST, aliases: dict) -> str | None:
    """`a.b.c` for a chain rooted at a plain Name (its alias canonicalised), else None (community :477)."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(aliases.get(cur.id, cur.id))
    return ".".join(reversed(parts))


def _is_module_path(path: str) -> bool:
    """`path` names a module object (an allowed module, a bpy namespace) rather than a value inside one."""
    if path in MODULE_PATHS:
        return True
    parts = path.split(".")
    if parts[0] != "bpy":
        return False
    if len(parts) == 2:
        return parts[1] in ("ops", "types", "utils", "props", "path", "msgbus", "app")
    return len(parts) == 3 and parts[1] == "ops"


def _context_hint(chain: str | None, default: str | None) -> str | None:
    """The replacement tool implied by where a file path or file call appears (render settings, images, fonts...)."""
    segs = set((chain or "").split("."))
    if segs & {"render", "image_settings"}:
        return "render"
    if "export" in (chain or "") or segs & {"export_scene", "export_mesh"}:
        return "export"
    if segs & {"fonts", "font"}:
        return "font"
    if segs & {"images", "image"}:
        return "image_save" if default != "image_load" else "image_load"
    if segs & {"texts"}:
        return "text"
    return default


def path_rule(path: str) -> tuple[str, str | None] | None:
    """(reason, hint key) when the canonical module-rooted chain `path` is denied (community :495, new policy)."""
    parts = path.split(".")
    root = parts[0]
    for seg in parts[1:]:
        if seg in REROOT_SEGMENTS:
            return f"{path} reaches a module through another module's attribute", None
        if seg in DANGEROUS_SEGMENTS:
            return f"{path} reaches the {seg!r} module", None
    for i in range(1, len(parts)):
        mod = ".".join(parts[:i])
        if parts[i] in ESCAPE_EDGES.get(mod, ()):
            if any(len(m.split(".")) > i + 1 and (path == m or path.startswith(m + ".")) for m in MODULE_PATHS):
                continue                      # numpy.lib.stride_tricks: an allowed module below a denied edge
            return f"{mod}.{parts[i]} leads outside the allowed modules", None
    if "types" in parts[1:] and not (path.startswith("bpy.types") or path.startswith("bmesh.types")):
        return f"{path} reaches the 'types' module", None
    if root == "bpy":
        for prefix, reason, hint in BPY_PATHS_DENIED:
            if path == prefix or path.startswith(prefix + "."):
                return f"{prefix}: {reason}", hint
        if len(parts) >= 3 and parts[1] == "ops":
            ns = parts[2]
            if ns in OPS_NAMESPACES_DENIED:
                reason, hint = OPS_NAMESPACES_DENIED[ns]
                return f"bpy.ops.{ns}: {reason}", hint
            if ns.startswith("import_"):
                return f"bpy.ops.{ns}: importing files is not scene code", "import_file"
            if ns.startswith("export_"):
                return f"bpy.ops.{ns}: exporting is a host tool", "export"
            if len(parts) >= 4:
                op = f"{ns}.{parts[3]}"
                if op in OPS_DENIED:
                    reason, hint = OPS_DENIED[op]
                    return f"bpy.ops.{op}: {reason}", hint
                if "import" in parts[3]:
                    return f"bpy.ops.{op}: importing files is not scene code", "import_file"
                if "export" in parts[3]:
                    return f"bpy.ops.{op}: exporting is a host tool", "export"
    elif root == "numpy":
        for seg in parts[1:]:
            if seg in DENIED_NAMES and not (seg == "lib" and path.startswith("numpy.lib.stride_tricks")):
                return f"{path}: {DENIED_NAMES[seg][0]}", DENIED_NAMES[seg][1] or "numpy_io"
    elif root == "g3d":
        rest = parts[1:]
        if rest[:1] == ["lib"]:
            rest = rest[1:]
        if rest and rest[0] not in G3D_LIB_NAMES:
            tool = {"export_glb": "export", "save_checkpoint": "checkpoint"}.get(rest[0], "g3d")
            return f"g3d.{rest[0]} is not part of the g3d facade (use the named host tool)", tool
    elif root == "string" and len(parts) > 1 and parts[1] == "Formatter":
        return "string.Formatter walks attribute names with the real getattr", None
    return None


def _name_rule(name: str, store: bool) -> tuple[str, str | None] | None:
    """(reason, hint) for one attribute name wherever it appears (receiver unknown)."""
    if name.startswith("_"):
        return f"private attribute {name!r} is not accessible", None
    if name in FORBIDDEN_ATTRS:
        return f"attribute {name!r} is an interpreter escape path", None
    if name in DENIED_NAMES:
        reason, hint = DENIED_NAMES[name]
        return f".{name}: {reason}", hint
    if store and name in ASSIGN_DENIED:
        reason, hint = ASSIGN_DENIED[name]
        return f"assigning .{name}: {reason}", hint
    if name in DANGEROUS_SEGMENTS:
        return f".{name} reaches the {name!r} module", None
    return None


def _collect_bindings(node: ast.AST) -> set[str]:
    """Names bound anywhere inside `node`, over-approximated across scopes (community :507)."""
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            found.add(child.id)
        elif isinstance(child, ast.arg):
            found.add(child.arg)
        elif isinstance(child, ast.FunctionDef):
            found.add(child.name)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            found.add(child.name)
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            for alias in child.names:
                if alias.name != "*":
                    found.add(alias.asname or alias.name.split(".")[0])
    return found


def _hint_text(key: str | None) -> str | None:
    return HINTS.get(key) if key else None


class _Validator(ast.NodeVisitor):
    """Deny-by-default walk over a pre-scanned binding set (community :533-832; violations are collected)."""

    def __init__(self, aliases: dict, parents: dict) -> None:
        self.aliases = aliases                     # local name -> canonical module path
        self.parents = parents                     # child node -> parent node
        self.bound: set[str] = set()
        self.functions: set[str] = set()
        self.from_imported: set[str] = set()
        self.violations: list[Violation] = []

    def fail(self, message: str, node: ast.AST | None, hint: str | None = None) -> None:
        self.violations.append(Violation(getattr(node, "lineno", None), message, _hint_text(hint)))

    def _bind(self, name: str, node: ast.AST) -> None:
        if name in self.aliases:
            self.fail(f"cannot rebind module name {name!r}; shadowing a module defeats the path checks", node)
        if name in FORBIDDEN_CALLABLES or name in ATTR_BUILTINS:
            self.fail(f"cannot define {name!r}: rebinding a blocked builtin smuggles the real one back", node)
        if name in ALLOWED_BUILTINS:
            self.fail(f"cannot rebind builtin name {name!r}; later calls to it would be unverifiable", node)
        if name.startswith("__") and name.endswith("__"):
            self.fail(f"cannot bind dunder name {name!r}", node)

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) not in ALLOWED_NODES:
            name = type(node).__name__
            self.fail(NODE_EXPLANATIONS.get(name, f"{name} is not on the allowlist"), node)
            return
        super().generic_visit(node)

    # -- imports -------------------------------------------------------------------------------------------------
    def _module_allowed(self, module: str, node: ast.AST) -> bool:
        root = module.split(".")[0]
        if root not in ALLOWED_MODULES:
            self.fail(f"import of {module!r} is not allowed (allowed: bpy, bmesh, mathutils, numpy, math, json, "
                      "collections, itertools, functools, re, random, statistics, ...)", node)
            return False
        if module not in MODULE_PATHS:
            self.fail(f"import of {module!r} is not allowed (allowed submodules: "
                      f"{', '.join(sorted(m for m in ALLOWED_SUBMODULES if m.startswith(root + '.')))})", node)
            return False
        return True

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if not self._module_allowed(alias.name, node):
                continue
            if alias.asname and alias.name.split(".")[0] == "bpy":
                self.fail(f"aliasing bpy ({alias.name} as {alias.asname}) hides it from the path checks; "
                          "use `import bpy`", node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self.fail("relative imports are not allowed", node)
            return
        module = node.module or ""
        if not self._module_allowed(module, node):
            return
        if module.split(".")[0] == "bpy":
            self.fail("from-imports of bpy are not allowed; use `import bpy` and full dotted paths", node)
            return
        for alias in node.names:
            name = alias.name
            if name == "*":
                self.fail("wildcard import hides what enters the namespace", node)
                continue
            if name.startswith("_") or name in DENIED_IMPORT_NAMES:
                self.fail(f"importing {name!r} from {module!r} is not allowed", node)
                continue
            rule = _name_rule(name, False)
            if rule:
                self.fail(f"importing {name!r} from {module!r}: {rule[0]}", node, rule[1])
                continue
            rule = path_rule(f"{module}.{name}")
            if rule:
                self.fail(rule[0], node, rule[1])
                continue
            if module in ("g3d", "g3d.lib") and name not in G3D_LIB_NAMES and f"{module}.{name}" != "g3d.lib":
                self.fail(f"{module}.{name} is not part of the g3d facade", node, "g3d")
            if f"{module}.{name}" not in MODULE_PATHS:
                self._bind(alias.asname or name, node)

    # -- names ---------------------------------------------------------------------------------------------------
    def visit_Name(self, node: ast.Name) -> None:
        name = node.id
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            if name not in PREBOUND_NAMES:
                self._bind(name, node)
            return
        if name in FORBIDDEN_CALLABLES:
            self.fail(f"{name} is forbidden: {FORBIDDEN_CALLABLES[name]}", node)
            return
        if name.startswith("__") and name.endswith("__"):
            self.fail(f"dunder name {name!r} is not accessible", node)
            return
        if name in self.aliases or name in self.bound or name in PREBOUND_NAMES:
            return
        if name in ALLOWED_BUILTINS or name in ATTR_BUILTINS:
            return
        self.fail(f"unknown name {name!r}; only allowed builtins, imported modules and names bound in this snippet "
                  "may be used (Python variables do not persist between calls; keep durable values in custom "
                  "properties)", node)

    # -- attributes ----------------------------------------------------------------------------------------------
    def visit_Attribute(self, node: ast.Attribute) -> None:
        store = isinstance(node.ctx, (ast.Store, ast.Del))
        path = _attr_chain(node, self.aliases)
        parent = self.parents.get(node)
        outermost = not (isinstance(parent, ast.Attribute) and parent.value is node)
        if outermost and path is not None and _chain_root_name(node) in self.aliases:
            rule = path_rule(path)
            if rule:
                self.fail(f"{path} is not allowed: {rule[0]}", node, rule[1])
            if store and path.startswith("bpy.types."):
                self.fail(f"assigning {path} registers a persistent RNA property", node)
        rule = _name_rule(node.attr, store)
        if rule:
            hint = _context_hint(_attr_chain(node, {}), rule[1]) if rule[1] in ("file_path", "image_save",
                                                                                 "numpy_io") else rule[1]
            if rule[1] == "numpy_io" and not (path or "").startswith("numpy"):
                hint = _context_hint(_attr_chain(node, {}), "image_load" if node.attr in ("load", "reload")
                                     else "image_save")
            self.fail(rule[0], node, hint)
        if path is None and node.attr in MODULE_NAVIGATION:
            self.fail(f"{node.attr!r} reached through an unresolvable receiver; module navigation must start from "
                      "a plain module name", node)
        self.generic_visit(node)

    # -- calls ---------------------------------------------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        for kw in node.keywords:
            if kw.arg in KWARGS_DENIED:
                chain = _attr_chain(func, self.aliases)
                rule = path_rule(chain) if chain and _chain_root_name(func) in self.aliases else None
                hint = rule[1] if rule and rule[1] else _context_hint(chain, KWARGS_DENIED[kw.arg])
                self.fail(f"keyword {kw.arg!r} is not allowed: file paths are host-controlled", node, hint)
        if isinstance(func, ast.Name):
            name = func.id
            if name in FORBIDDEN_CALLABLES:
                pass                                   # reported once by visit_Name on the callee
            elif name in ATTR_BUILTINS:
                if name != "hasattr":                  # a bool answer grants nothing; any string name is fine
                    self._check_attr_call(name, node)
            elif name == "type" and len(node.args) != 1:
                self.fail("type() with three arguments creates a class", node)
            elif not (name in self.functions or name in self.aliases or name in self.from_imported
                      or name in ALLOWED_BUILTINS):
                self.fail(f"{name!r} is not a known callable; call a def of this snippet, an allowed builtin or an "
                          "attribute of an imported module (a lambda is a value, never a call target)", node)
        elif isinstance(func, ast.Attribute):
            if func.attr == "format":
                recv = func.value
                if not (isinstance(recv, ast.Constant) and isinstance(recv.value, str)):
                    self.fail(".format() only on a string literal (use an f-string)", node)
                else:
                    try:
                        pending = [recv.value]
                        while pending:
                            for _, field, spec, _ in string.Formatter().parse(pending.pop()):
                                # Nested replacement fields in a format spec also perform C-level getattr.
                                if spec and ("{" in spec or "}" in spec):
                                    pending.append(spec)
                                for segment in (field or "").replace("[", ".").replace("]", ".").split("."):
                                    rule = _name_rule(segment, False)
                                    if rule:
                                        self.fail(f"a format field reaches a forbidden attribute: {rule[0]}", node, rule[1])
                    except ValueError:
                        self.fail("malformed format string", node)
        else:
            self.fail("call target must be a name or an attribute; computed call targets cannot be validated", node)
        self.generic_visit(node)

    def _check_attr_call(self, name: str, node: ast.Call) -> None:
        """getattr family: a LITERAL name is checked now; a computed one is checked at run time (RuntimeGuard)."""
        if len(node.args) < 2:
            self.fail(f"{name}() needs an object and an attribute name", node)
            return
        target = node.args[1]
        if isinstance(target, ast.Constant):
            if not isinstance(target.value, str):
                self.fail(f"{name}() attribute name must be a string", node)
                return
            attr = target.value
            rule = _name_rule(attr, name in ("setattr", "delattr"))
            if rule:
                self.fail(f"{name}(): {rule[0]}", node, rule[1])
            if attr in MODULE_NAVIGATION:
                self.fail(f"{name}() targets namespace {attr!r}; namespaces are reached through dotted paths", node)

    # -- functions -----------------------------------------------------------------------------------------------
    def visit_arg(self, node: ast.arg) -> None:
        self._bind(node.arg, node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.decorator_list:
            self.fail("decorators are not allowed; they apply arbitrary callables to functions", node)
        if getattr(node, "type_params", None):
            self.fail("type parameters are not needed", node)
        self._bind(node.name, node)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._bind(node.name, node)
        self.generic_visit(node)


def _chain_root_name(node: ast.AST) -> str | None:
    cur = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    return cur.id if isinstance(cur, ast.Name) else None


def _parents(tree: ast.AST) -> dict:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _check_module_value_use(tree: ast.AST, v: _Validator) -> None:
    """A module object may only start an attribute chain (community :834-891): a module name or a chain that ends on
    a module (bpy.ops, bpy.ops.object, np.random, mathutils.geometry) is never a value, so every later attribute
    access stays rooted at the module name where the path rules can see it."""
    parents = v.parents

    def chain_root(node: ast.AST) -> bool:
        parent = parents.get(node)
        return isinstance(parent, ast.Attribute) and parent.value is node

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in v.aliases \
                and not chain_root(node):
            v.fail(f"module {node.id!r} may only start a dotted path; passing a module object around defeats the "
                   "path checks", node)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and not chain_root(node):
            if _chain_root_name(node) not in v.aliases:
                continue
            path = _attr_chain(node, v.aliases)
            if path is None:
                continue
            parts = path.split(".")
            if (parts[0] == "bpy" and len(parts) > 1 and all(p in MODULE_NAVIGATION for p in parts[1:])) \
                    or _is_module_path(path):
                v.fail(f"{path} is a module namespace and may not be used as a value; continue the path to a "
                       "concrete attribute or call", node)


def _guard_source(code) -> tuple[ast.Module | None, Violation | None]:
    """Parse with size limits applied before and after (community :894-936)."""
    if not isinstance(code, str):
        return None, Violation(None, f"code must be a string, got {type(code).__name__}")
    if len(code.encode("utf-8", errors="replace")) > MAX_CODE_BYTES:
        return None, Violation(None, f"snippet exceeds {MAX_CODE_BYTES} bytes")
    if "\x00" in code:
        return None, Violation(None, "snippet contains a NUL byte")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return None, Violation(exc.lineno, f"syntax error: {exc.msg}")
    except (ValueError, MemoryError, RecursionError) as exc:
        return None, Violation(None, f"unparseable snippet: {type(exc).__name__}")
    count = 0
    for _ in ast.walk(tree):
        count += 1
        if count > MAX_AST_NODES:
            return None, Violation(None, f"snippet exceeds {MAX_AST_NODES} AST nodes")
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_NESTING_DEPTH:
            return None, Violation(getattr(node, "lineno", None), f"snippet nests deeper than {MAX_NESTING_DEPTH}")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree, None


def _aliases(tree: ast.Module, v_fail) -> dict:
    """Prebound modules plus every import binding (`import numpy as np`, `from numpy import linalg`). A prebound name
    other than bpy that the snippet binds itself and never imports (Astra's `np = xy + new[j]` in the Studio corpus)
    is that snippet's own variable: it is dropped here and the worker does not prebind it."""
    aliases = dict(PREBOUND_MODULES)
    own = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
    own |= {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}
    own |= {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    own |= {n.name for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.name}
    for name in list(aliases):
        if name != "bpy" and name in own:
            del aliases[name]

    def add(local: str, target: str, node) -> None:
        if local in aliases and aliases[local] != target:
            v_fail(f"{local!r} already names module {aliases[local]!r}; a name may alias one module only", node)
            return
        aliases[local] = target

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_MODULES:
                    continue
                if alias.asname:
                    if root != "bpy":
                        add(alias.asname, alias.name, node)
                else:
                    add(root, root, node)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if full in MODULE_PATHS:
                    add(alias.asname or alias.name, full, node)
    return aliases


def _callable_names(tree: ast.Module, parents: dict, aliases: dict, functions: set, from_imported: set) -> set:
    """Names that may be called besides defs, from-imports, modules and builtins: function parameters (Astra passes
    its own defs as arguments, `sweep(..., uvx=uv_rot)`), and names whose EVERY binding is a plain assignment of a
    def, a from-import or a module-rooted attribute chain (`nz = noise.noise`). Safe because every callable is checked
    where it is referenced (path and name rules statically, computed getattr at run time); a lambda-bound name, a
    loop variable, an unpacked name or a computed call target (`f()()`, `fns[0]()`) stays refused."""
    good: set[str] = {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}
    assigned_ok: set[str] = set()
    assigned_bad: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)):
            continue
        parent = parents.get(node)
        value = None
        if isinstance(parent, ast.Assign) and node in parent.targets:
            value = parent.value
        elif isinstance(parent, ast.AnnAssign) and node is parent.target:
            value = parent.value
        if isinstance(value, ast.Attribute) and _chain_root_name(value) in aliases:
            assigned_ok.add(node.id)
        elif isinstance(value, ast.Name) and (value.id in functions or value.id in from_imported):
            assigned_ok.add(node.id)
        else:
            assigned_bad.add(node.id)
    return good | (assigned_ok - assigned_bad)


@dataclass(frozen=True)
class Analysis:
    violations: list
    prebound: dict          # the prebound module names this snippet uses as modules (the worker binds only these)


def analyse(code) -> Analysis:
    """Every violation of the safe-mode policy in `code` (source order) and the prebound names it keeps."""
    tree, early = _guard_source(code)
    if early is not None:
        return Analysis([early], {})
    pre: list[Violation] = []
    aliases = _aliases(tree, lambda m, n: pre.append(Violation(getattr(n, "lineno", None), m)))
    parents = _parents(tree)
    v = _Validator(aliases, parents)
    v.violations.extend(pre)
    v.bound = _collect_bindings(tree) - set(aliases)
    v.functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    v.from_imported = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                       for a in n.names if a.name != "*"} - set(aliases)
    v.functions |= _callable_names(tree, parents, aliases, v.functions, v.from_imported)
    _check_module_value_use(tree, v)
    v.visit(tree)
    seen: set = set()
    out: list[Violation] = []
    for item in sorted(v.violations, key=lambda x: (x.line or 0)):
        key = (item.line, item.use_instead) if item.use_instead else (item.line, item.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    prebound = {k: m for k, m in PREBOUND_MODULES.items() if aliases.get(k) == m}
    return Analysis(out, prebound)


def validate(code) -> list[Violation]:
    """Every violation of the safe-mode policy in `code`, in source order; [] when the snippet may run."""
    return analyse(code).violations



# ------------------------------------------------------------------------------------------------ runtime layer 1-2
def _module_ok(name: str) -> bool:
    return name in MODULE_PATHS


class RuntimeGuard:
    """Restricted builtins for one snippet: allowlisted builtins, a guarded `__import__` and wrapped
    get/set/has/delattr that check COMPUTED names against the same deny sets as the static walk (measured:
    `getattr(bpy.data, 'te'+'xts')` is refused here). Every refusal is recorded, so a snippet that catches the
    PermissionError still ends `refused_runtime`."""

    def __init__(self, g3d_facade=None) -> None:
        self.refusals: list[str] = []
        self.g3d_facade = g3d_facade

    def _refuse(self, message: str, hint: str | None = None):
        self.refusals.append(message)
        text = HINTS.get(hint) if hint else None
        raise PermissionError(message + (f" (use instead: {text})" if text else ""))

    def check_name(self, obj, name, setting: bool) -> None:
        if not isinstance(name, str):
            self._refuse("attribute names must be strings")
        rule = _name_rule(name, setting)
        if rule:
            self._refuse(rule[0], rule[1])
        if name in MODULE_NAVIGATION and isinstance(obj, types.ModuleType):
            self._refuse(f"{obj.__name__}.{name} is a namespace; reach it through a dotted path")
        if isinstance(obj, types.ModuleType):
            rule = path_rule(f"{obj.__name__}.{name}")
            if rule:
                self._refuse(f"{obj.__name__}.{name}: {rule[0]}", rule[1])

    def _check_value(self, value):
        if isinstance(value, types.ModuleType) and not _module_ok(value.__name__):
            self._refuse(f"a computed attribute led to module {value.__name__!r}")
        return value

    def builtins(self) -> dict:
        b = {n: getattr(_py_builtins, n) for n in ALLOWED_BUILTINS if hasattr(_py_builtins, n)}
        guard = self

        def g_getattr(obj, name, *default):
            guard.check_name(obj, name, False)
            return guard._check_value(getattr(obj, name, *default))

        def g_hasattr(obj, name):
            if not isinstance(name, str):
                raise TypeError("hasattr(): attribute name must be string")
            return hasattr(obj, name)

        def g_setattr(obj, name, value):
            guard.check_name(obj, name, True)
            setattr(obj, name, value)

        def g_delattr(obj, name):
            guard.check_name(obj, name, True)
            delattr(obj, name)

        real_import = _py_builtins.__import__

        def g_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level or not isinstance(name, str) or name not in MODULE_PATHS:
                guard._refuse(f"import of {name!r} is not allowed")
            for item in fromlist or ():
                if not isinstance(item, str) or item.startswith("_") or item in DENIED_IMPORT_NAMES \
                        or _name_rule(item, False) or path_rule(f"{name}.{item}"):
                    guard._refuse(f"importing {item!r} from {name!r} is not allowed")
            if name.split(".")[0] == "g3d":
                if guard.g3d_facade is None:
                    guard._refuse("the g3d facade is not available in this worker")
                return guard.g3d_facade
            return real_import(name, globals, locals, fromlist, level)

        b.update(getattr=g_getattr, hasattr=g_hasattr, setattr=g_setattr, delattr=g_delattr, __import__=g_import)
        return b


# ------------------------------------------------------------------------------------------------ runtime layer 3
AUDIT_DENY_PREFIXES = (
    "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.startfile", "os.kill", "os.remove",
    "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.chdir", "os.truncate", "os.link", "os.symlink",
    "os.utime", "os.listdir", "os.scandir", "os.putenv", "os.unsetenv", "os.add_dll_directory", "os.chflags",
    "glob.", "shutil.", "subprocess.", "_winapi.", "msvcrt.", "socket.", "ctypes.", "winreg.", "urllib.",
    "http.client.", "ftplib.", "smtplib.", "webbrowser.", "sys.addaudithook", "sys.settrace", "sys.setprofile",
    "sys._getframe", "sys._current_frames", "code.__new__", "marshal.", "pickle.", "builtins.input",
    "tempfile.", "sqlite3.", "mmap.")
_ESCAPE_GETATTR = FORBIDDEN_ATTRS | {"__code__", "__globals__", "__builtins__", "__closure__", "__subclasses__"}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


class AuditGuard:
    """A `sys.addaudithook` that refuses, while ARMED and only on the arming thread: write opens anywhere, read opens
    outside `read_roots` (the Blender install with its stdlib and numpy, and glasses3d/g3d), and process, socket,
    ctypes, winreg, filesystem-mutation and frame-introspection events. Armed only while Astra's code runs; trusted
    worker methods run disarmed. Every refusal is recorded (a snippet that catches the PermissionError is still
    refused at the end of the call). A hook cannot be removed once installed, so one guard per process."""

    def __init__(self, read_roots) -> None:
        self.read_roots = tuple(os.path.normcase(os.path.abspath(str(r))) + os.sep for r in read_roots)
        self.armed = False
        self.thread = None
        self.refusals: list[str] = []
        self.installed = False

    def install(self) -> None:
        if not self.installed:
            sys.addaudithook(self._hook)
            self.installed = True

    def arm(self) -> None:
        self.refusals = []
        self.thread = threading.get_ident()
        self.armed = True

    def disarm(self) -> list[str]:
        self.armed = False
        return list(self.refusals)

    def _readable(self, path) -> bool:
        p = os.path.normcase(os.path.abspath(path)) + os.sep
        return any(p.startswith(root) for root in self.read_roots)

    def _refuse(self, what: str):
        self.refusals.append(what)
        raise PermissionError(f"{what} is not available to scene code")

    def _hook(self, event: str, args) -> None:
        if not self.armed or threading.get_ident() != self.thread:
            return
        if event == "open":
            path = args[0] if args else None
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            if path is None or isinstance(path, int):
                return
            try:
                spath = os.fsdecode(path)
            except (TypeError, ValueError):
                self._refuse("open of an unreadable path")
                return
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                (mode is None and isinstance(flags, int) and bool(flags & _WRITE_FLAGS))
            if writing:
                self._refuse(f"opening {spath!r} for writing")
            if not self._readable(spath):
                self._refuse(f"reading {spath!r}")
            return
        if event == "object.__getattr__":
            if len(args) > 1 and args[1] in _ESCAPE_GETATTR:
                self._refuse(f"reading {args[1]!r}")
            return
        if event.startswith(AUDIT_DENY_PREFIXES):
            self._refuse(f"{event}")
