#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

#  What I really want to do is clean for release. So replace explicit paths with references to CMAKE_CURRENT_SOURCE_DIR
# in cMake files, and also delete some extra files that are spewed out by various installers. The various licenses
# should probably be consolidated.

import os
import re
import shutil
from typing import Dict, List

paths_to_delete = [
    "custom_vc14_64.bat",
    "custom.bat",
    "USING_HDF5_CMake.txt",
    "USING_HDF5_VS.txt",
    "env.bat",
    "draw.bat",
    "RELEASE.txt",
    "samples",
]


def delete_extraneous_files(base_path: str) -> None:
    """Delete each of the files or directories listed above from the path specified in base_path. Failure to delete an
    entry does not constitute a fatal error."""
    print("Removing extraneous files")
    if not os.path.exists(base_path):
        raise RuntimeError(f"{base_path} does not exist")
    if not os.path.isdir(base_path):
        raise RuntimeError(f"{base_path} is not a directory")
    for entry in paths_to_delete:
        target = os.path.join(base_path, entry)
        if not os.path.lexists(target):
            continue
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.unlink(target)
        except OSError:
            # If the entry cannot be removed, that's not a fatal error.
            pass


def remove_local_path_from_cmake_files(base_path: str) -> None:
    """In many cases, the local compilation paths get stored into the cMake files. They should not ever be used, but
    a) OpenCASCADE codes in the local path to FreeType, which then fails when the LibPack is distributed, and b) for
    good measure cMake files shouldn't refer to non-existent paths on a foreign system. So this method looks for
    cmake config files and cleans the ones it finds."""
    print("Removing local paths from cMake files")
    for root, dirs, files in os.walk(base_path):
        for file in files:
            if file.lower().endswith(".cmake"):
                remove_local_path_from_cmake_file(base_path, os.path.join(root, file))


def remove_local_path_from_cmake_file(base_path: str, file_to_clean: str) -> None:
    """Modify a cMake file to remove base_path and replace it with ${CMAKE_CURRENT_SOURCE_DIR}. WARNING: effectively
    edits the file in-place, no backup is made."""
    depth_string = create_depth_string(base_path, file_to_clean)
    with open(file_to_clean, "r", encoding="UTF-8") as f:
        contents = f.read()

    base_path_native = base_path.rstrip("\\/")
    cmake_replacement = "${CMAKE_CURRENT_SOURCE_DIR}/" + depth_string[:-1]
    contents = contents.replace(base_path_native, cmake_replacement)
    cmake_base_path = base_path_native.replace("\\", "/")
    if cmake_base_path != base_path_native:
        contents = contents.replace(cmake_base_path, cmake_replacement)

    contents = _normalize_slashes_in_cmake_paths(contents)

    with open(file_to_clean, "w", encoding="utf-8") as f:
        f.write(contents)


_QUOTED_CMAKE_PATH_RE = re.compile(r'"([^"\n]*\$\{CMAKE_CURRENT_SOURCE_DIR\}[^"\n]*)"')


def _normalize_slashes_in_cmake_paths(contents: str) -> str:
    """Some upstreams (Netgen, others) record Windows-style paths with backslashes
    in their installed CMake configs. After the prefix substitution above, the
    install-dir portion is replaced with `${CMAKE_CURRENT_SOURCE_DIR}/...` but any
    trailing backslashes inside the quoted string remain, producing strings such
    as `"${CMAKE_CURRENT_SOURCE_DIR}/..\\bin\\python_d.exe"`. CMake accepts
    forward slashes universally on Windows; quoted strings that contain a
    `${CMAKE_CURRENT_SOURCE_DIR}` reference are paths, not CMake escape sequences,
    so it is safe to normalize backslashes within them."""

    def _normalize(match: re.Match) -> str:
        return '"' + match.group(1).replace("\\", "/") + '"'

    return _QUOTED_CMAKE_PATH_RE.sub(_normalize, contents)


def create_depth_string(base_path: str, file_to_clean: str) -> str:
    """Given a base path and a file, determine how many "../" must be appended to the file's containing directory
    to result in a path that resolves to base_path. Returns a string containing just some number of occurrences of
    "../" e.g. "../../../" to move up three levels from file_to_clean's containing folder."""

    file_norm = file_to_clean.replace("\\", "/")
    while "//" in file_norm:
        file_norm = file_norm.replace("//", "/")
    base_norm = base_path.replace("\\", "/").rstrip("/")

    if not file_norm.startswith(base_norm):
        raise RuntimeError(f"{file_to_clean} does not appear to be in {base_path}")

    containing_directory = file_norm.rsplit("/", 1)[0] if "/" in file_norm else ""
    directories_to_file = len(containing_directory.split("/"))
    directories_in_base = len(base_norm.split("/"))
    num_steps_up = directories_to_file - directories_in_base
    return "../" * num_steps_up


def correct_opencascade_freetype_ref(base_path: str):
    """OpenCASCADE hardcodes the path to the freetype it was compiled against. The above code doesn't correct it to
    the necessary path because of the way this variable is used within cMake. So just remove the path altogether and
    rely on the rest of our configuration to find the correct one."""
    files_to_fix = ["OpenCASCADEDrawTargets.cmake", "OpenCASCADEVisualizationTargets.cmake"]
    for fix in files_to_fix:
        path = os.path.join(base_path, "cmake", fix)
        with open(path, "r", encoding="utf-8") as f:
            contents = f.read()
        contents = contents.replace(
            "${CMAKE_CURRENT_SOURCE_DIR}/../lib/freetype.lib", "freetype.lib"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(contents)


def delete_qtwebengine(base_path: str):
    """QtWebEngine is huge and pervasive -- it's also not used by FreeCAD (anymore). Delete anything that seems to be
    related to it from the LibPack."""

    print("Removing QtWebEngine (and related code)")
    for root, dirs, files in os.walk(base_path):
        for dir in dirs:
            if (
                "webengine" in dir.lower()
                or "webchannel" in dir.lower()
                or "websockets" in dir.lower()
            ):
                try:
                    full_path = os.path.join(root, dir)
                    shutil.rmtree(full_path)
                except OSError as e:
                    print(f"Failed to delete file {full_path}: {e}")
        for file in files:
            if "webengine" in file.lower() or "webchannel" in file.lower():
                try:
                    full_path = os.path.join(root, file)
                    os.unlink(full_path)
                except OSError as e:
                    print(f"Failed to delete path {full_path}: {e}")


def delete_qtquick(base_path: str):
    """QtQuick is unused in FreeCAD at this time."""

    def is_qtquick(name: str) -> bool:
        lc = name.lower()
        if "qtquick" in lc or "qml" in lc:
            return True
        if lc.startswith("q") and "quick" in lc:
            return True
        return False

    print("Removing QtQuick/QML")
    for root, dirs, files in os.walk(base_path):
        for dir in dirs:
            if is_qtquick(dir):
                try:
                    full_path = os.path.join(root, dir)
                    shutil.rmtree(full_path)
                except OSError as e:
                    print(f"Failed to delete file {full_path}: {e}")
        for file in files:
            if is_qtquick(file):
                try:
                    full_path = os.path.join(root, file)
                    os.unlink(full_path)
                except OSError as e:
                    print(f"Failed to delete path {full_path}: {e}")


def delete_llvm_executables(base_path: str):
    """During the build of the libpack, a number of llvm executable files are created: these are not needed to compile
    or run FreeCAD, so remove them."""
    print("Removing llvm executables")
    files_in_bin = os.listdir(os.path.join(base_path, "bin"))
    for file in files_in_bin:
        if file.startswith("llvm") and file.endswith(".exe"):
            try:
                os.unlink(os.path.join(base_path, "bin", file))
            except OSError as e:
                pass


def delete_clang_executables(base_path: str):
    """During the build of the libpack, a number of clang executable files are created: these are not needed to compile
    or run FreeCAD, so remove them."""
    print("Removing clang executables")
    files_in_bin = os.listdir(os.path.join(base_path, "bin"))
    for file in files_in_bin:
        if file.startswith("clang") and file.endswith(".exe"):
            try:
                os.unlink(os.path.join(base_path, "bin", file))
            except OSError as e:
                pass


UNUSED_STATIC_LIB_PREFIXES = ("clang", "LLVM", "clazy", "lld")


def delete_unused_static_libs(base_path: str) -> int:
    """Remove the LLVM, Clang, LLD, and clazy static libraries from lib/.

    FreeCAD links against libclang's stable C ABI through libclang.dll only. The clang*.lib, LLVM*.lib, lld*.lib, and
    clazy*.lib files in lib/ are the internal C++ static libraries used to build other LLVM-based tools, and are not
    consumed by FreeCAD or any of its dependencies. Removing them saves roughly one gigabyte from the LibPack.

    Returns the number of files actually removed, which is useful for logging and for the unit tests.
    """
    print("Removing unused LLVM, Clang, LLD, and clazy static libraries")
    lib_dir = os.path.join(base_path, "lib")
    if not os.path.isdir(lib_dir):
        return 0

    removed = 0
    for entry in os.listdir(lib_dir):
        if not entry.lower().endswith(".lib"):
            continue
        if not entry.startswith(UNUSED_STATIC_LIB_PREFIXES):
            continue
        full_path = os.path.join(lib_dir, entry)
        if not os.path.isfile(full_path):
            continue
        try:
            os.unlink(full_path)
            removed += 1
        except OSError as e:
            print(f"Failed to delete {full_path}: {e}")
    return removed


_DOCUMENTATION_RELATIVE_DIRS = (
    os.path.join("doc"),
    os.path.join("share", "doc"),
)


def delete_documentation(base_path: str) -> int:
    """Remove the human-readable documentation trees that upstream installers leave behind.

    Three sources contribute to these directories:
      - share/doc/med-fichier-<version>/ holds roughly 110 megabytes of generated MED HTML and PDF.
      - share/doc/{gmsh,pcre2,zlib,xerces-c,clazy} ship reference manuals and READMEs.
      - doc/{config,global}/ at the LibPack root is Qt's qdoc input configuration: HTML templates, theme assets,
        and per-module URL definitions used by qdoc.exe to produce documentation.

    None of this material is consumed when FreeCAD or any of its dependencies are built or run, so both directory
    trees are removed wholesale. Returns the number of top-level directories removed."""
    print("Removing bundled documentation trees")
    removed = 0
    for relative in _DOCUMENTATION_RELATIVE_DIRS:
        target = os.path.join(base_path, relative)
        if not os.path.isdir(target):
            continue
        try:
            shutil.rmtree(target)
            removed += 1
        except OSError as e:
            print(f"Failed to delete {target}: {e}")
    return removed


def delete_occt_sample_data(base_path: str) -> bool:
    """Remove the top-level data/ directory of OpenCASCADE sample geometry.

    OCCT installs a 50 MB collection of demonstration BREP, STL, IGES, STEP, VRML, and image files under data/.
    These are inputs for OCCT's Tcl tutorial scripts, which are themselves removed by delete_extraneous_files via
    the samples/ entry in paths_to_delete. With the consuming scripts gone, the data tree is dead weight.

    Returns True if the directory was found and removed."""
    print("Removing OCCT sample data")
    target = os.path.join(base_path, "data")
    if not os.path.isdir(target):
        return False
    try:
        shutil.rmtree(target)
        return True
    except OSError as e:
        print(f"Failed to delete {target}: {e}")
        return False


def delete_lldb(base_path: str) -> int:
    """Remove the LLDB debugger runtime files.

    The LLVM toolchain ships LLDB as the bundled liblldb.dll plus a Python bindings package. FreeCAD does not embed
    or attach a debugger, so the entire LLDB runtime is dead weight, accounting for roughly 175 megabytes of DLLs and
    a similarly sized Python package.

    Returns the number of paths removed."""
    print("Removing LLDB runtime")
    targets = [
        os.path.join(base_path, "bin", "liblldb.dll"),
        os.path.join(base_path, "bin", "liblldb-original.dll"),
        os.path.join(base_path, "lib", "site-packages", "lldb"),
        os.path.join(base_path, "bin", "Lib", "site-packages", "lldb"),
    ]
    removed = 0
    for target in targets:
        if not os.path.lexists(target):
            continue
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.unlink(target)
            removed += 1
        except OSError as e:
            print(f"Failed to delete {target}: {e}")
    return removed


def delete_bundled_cmake(base_path: str) -> bool:
    """Remove the cmake pip package from the bundled Python.

    Some pip dependency pulls in the cmake package as a transitive build requirement. It installs a complete CMake
    distribution (the cmake.exe binary, modules, templates, and roughly nine megabytes of HTML documentation) under
    bin/Lib/site-packages/cmake, accounting for about 90 megabytes that FreeCAD never uses. Developers building
    FreeCAD provide their own CMake installation.

    Returns True if the package was found and removed."""
    print("Removing bundled cmake pip package")
    target = os.path.join(base_path, "bin", "Lib", "site-packages", "cmake")
    if not os.path.isdir(target):
        return False
    try:
        shutil.rmtree(target)
        return True
    except OSError as e:
        print(f"Failed to delete {target}: {e}")
        return False


_LLVM_INTERNAL_HEADER_DIRS = ("clang", "clang-tidy", "llvm", "lldb")


def delete_llvm_internal_headers(base_path: str) -> int:
    """Remove the LLVM, Clang, Clang-Tidy, and LLDB internal C++ headers from include/.

    FreeCAD consumes libclang only through the stable C ABI exposed by include/clang-c/ and include/llvm-c/. The
    sibling include/clang/, include/clang-tidy/, include/llvm/, and include/lldb/ trees expose the internal C++ APIs
    used to build LLVM-based tools and are never included by FreeCAD or any of its dependencies. The C ABI directories
    (clang-c and llvm-c) are intentionally preserved.

    Returns the number of directories removed."""
    print("Removing internal LLVM, Clang, Clang-Tidy, and LLDB headers")
    include_dir = os.path.join(base_path, "include")
    if not os.path.isdir(include_dir):
        return 0

    removed = 0
    for name in _LLVM_INTERNAL_HEADER_DIRS:
        target = os.path.join(include_dir, name)
        if not os.path.isdir(target):
            continue
        try:
            shutil.rmtree(target)
            removed += 1
        except OSError as e:
            print(f"Failed to delete {target}: {e}")
    return removed


_VC_INTERMEDIATE_PDB_RE = re.compile(r"^vc\d+\.pdb$", re.IGNORECASE)


def install_pdb_sidecars(
    install_dir: str, working_dir: str, extra_search_dirs: List[str] = None
) -> int:
    """Copy upstream debug-symbol files (PDBs) next to their installed DLLs.

    Most upstream CMake configs install only the DLL and the import library and
    leave the sidecar PDB behind in the build tree. As a result the installed
    LibPack carries no debugging information for Qt, OCCT, Coin, VTK, Boost, or
    the Python interpreter, even when built with /Zi /DEBUG. This walk fills
    that gap: for every DLL already installed under ``install_dir``, locate a
    PDB whose stem matches anywhere under ``working_dir`` (or any of the
    ``extra_search_dirs``) and whose modification time is newest, then copy it
    next to the DLL. Intermediate cl.exe PDBs (``vcNNN.pdb``) are skipped
    because they describe per-build compilations rather than a target's debug
    info. The ``extra_search_dirs`` argument exists because some packages
    (Qt in particular) build into an out-of-tree ``fallback-build-dir`` to dodge
    Windows path-length limits, and their PDBs are therefore not under
    ``working_dir``.

    Returns the number of PDB files newly placed."""
    print("Installing PDB debug-symbol sidecars")
    installed_dlls: Dict[str, str] = {}
    for root, _dirs, files in os.walk(install_dir):
        for name in files:
            if name.lower().endswith(".dll"):
                stem = os.path.splitext(name)[0].lower()
                installed_dlls.setdefault(stem, os.path.join(root, name))

    install_norm = os.path.normcase(os.path.abspath(install_dir))
    pdb_index: Dict[str, str] = {}
    search_roots = [working_dir]
    if extra_search_dirs:
        search_roots.extend(extra_search_dirs)
    for search_root in search_roots:
        if not os.path.isdir(search_root):
            continue
        for root, _dirs, files in os.walk(search_root):
            if os.path.normcase(os.path.abspath(root)).startswith(install_norm):
                continue
            for name in files:
                if not name.lower().endswith(".pdb"):
                    continue
                if _VC_INTERMEDIATE_PDB_RE.match(name):
                    continue
                stem = os.path.splitext(name)[0].lower()
                full = os.path.join(root, name)
                existing = pdb_index.get(stem)
                if existing is None or os.path.getmtime(full) > os.path.getmtime(existing):
                    pdb_index[stem] = full

    copied = 0
    for stem, dll_install_path in installed_dlls.items():
        pdb_source = pdb_index.get(stem)
        if pdb_source is None and stem.endswith("d"):
            # OpenCASCADE names its debug DLLs with a `d` suffix (e.g. TKerneld.dll)
            # but emits PDBs without it (TKernel.pdb). Try the stripped stem too.
            pdb_source = pdb_index.get(stem[:-1])
        if pdb_source is None:
            continue
        pdb_dest = os.path.splitext(dll_install_path)[0] + ".pdb"
        if os.path.exists(pdb_dest):
            continue
        try:
            shutil.copy2(pdb_source, pdb_dest)
            copied += 1
        except OSError as e:
            print(f"Failed to copy {pdb_source} to {pdb_dest}: {e}")
    print(f"  Installed {copied} PDB sidecars")
    return copied


def move_pdbs_to_sidecar(base_path: str, sidecar_path: str) -> int:
    """Move every .pdb under ``base_path`` into ``sidecar_path``, preserving relative paths.
    Returns the number of files moved. Used by Release builds to ship PDBs separately."""
    print(f"Moving PDB debug-symbol files to sidecar: {sidecar_path}")
    moved = 0
    base_abs = os.path.abspath(base_path)
    for root, _, files in os.walk(base_path):
        for name in files:
            if not name.lower().endswith(".pdb"):
                continue
            full_path = os.path.join(root, name)
            relative = os.path.relpath(full_path, base_abs)
            dest = os.path.join(sidecar_path, relative)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                shutil.move(full_path, dest)
                moved += 1
            except OSError as e:
                print(f"Failed to move {full_path} to {dest}: {e}")
    print(f"  Moved {moved} PDB files")
    return moved


def delete_pdb_files(base_path: str) -> int:
    """Remove every Microsoft debug-symbol (.pdb) file from the LibPack.

    PDB files are useful for crash-dump symbolication but are never required to compile or run FreeCAD. They account
    for roughly 150 megabytes spread across the bundled Python interpreter, ICU, OpenSSL, debugpy, and various Qt
    helper executables. A separate debugging LibPack carries them when needed; the release LibPack does not.

    Returns the number of files removed."""
    print("Removing PDB debug-symbol files")
    removed = 0
    for root, _, files in os.walk(base_path):
        for name in files:
            if not name.lower().endswith(".pdb"):
                continue
            full_path = os.path.join(root, name)
            try:
                os.unlink(full_path)
                removed += 1
            except OSError as e:
                print(f"Failed to delete {full_path}: {e}")
    return removed


_PYTHON_TEST_DIR_NAMES = frozenset({"test", "tests"})


def delete_python_test_suites(base_path: str) -> int:
    """Remove embedded test suites from the bundled Python distribution.

    Two locations are pruned:
      - bin/Lib/test, the Python standard library's own self-test suite, which is never needed at runtime.
      - bin/Lib/site-packages/**/test and .../tests, the per-package test directories shipped by scientific Python
        wheels (numpy, scipy, matplotlib, shapely, nltk, etc.). These contain test data, baseline images, and pytest
        modules that are not imported during normal use.

    Boost's test header library at include/boost-X_Y/boost/test is intentionally left in place because it is a usable
    public library, not a test suite for FreeCAD's dependencies.

    Returns the number of directories removed."""
    print("Removing Python test suites")
    removed = 0

    stdlib_test = os.path.join(base_path, "bin", "Lib", "test")
    if os.path.isdir(stdlib_test):
        try:
            shutil.rmtree(stdlib_test)
            removed += 1
        except OSError as e:
            print(f"Failed to delete {stdlib_test}: {e}")

    site_packages = os.path.join(base_path, "bin", "Lib", "site-packages")
    if not os.path.isdir(site_packages):
        return removed

    for root, dirs, _ in os.walk(site_packages, topdown=True):
        matches = [d for d in dirs if d.lower() in _PYTHON_TEST_DIR_NAMES]
        for match in matches:
            full_path = os.path.join(root, match)
            try:
                shutil.rmtree(full_path)
                removed += 1
            except OSError as e:
                print(f"Failed to delete {full_path}: {e}")
        dirs[:] = [d for d in dirs if d not in matches]

    return removed
