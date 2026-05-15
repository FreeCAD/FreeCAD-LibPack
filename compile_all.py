#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

# Every package has its own compilation and installation idiosyncrasies, so we have to use a custom
# build script for each one.

from diff_match_patch import diff_match_patch
from typing import Dict, List, Optional, Tuple

from enum import Enum
import glob
import os
import pathlib
import platform
import re
import shutil
import subprocess
import stat
import sys

# Pip requirements skipped in Debug mode because their PyPI distribution is a release-ABI
# wheel (cp3XX) that cannot install against the Py_DEBUG (cp3XXd) interpreter. These will
# be source-built against the debug Python in a later phase (debug_build_plan.md Phase 3).
# Names are matched case-insensitively against the package portion of each requirement
# specifier in config.json. Trim this set as each package gains a working source-build.
_DEBUG_BUILD_EXCLUDED_REQUIREMENTS = frozenset(
    name.lower()
    for name in (
        # Direct C/C++/Fortran/Rust extensions with no pure-Python distribution.
        "cmake",
        "cog",
        "ifcopenshell",
        "shapely",
    )
)

# Build-time tooling that must be present in the LibPack so that pip's --no-build-isolation
# can resolve PEP 517 build backends locally. setuptools covers most packages; meson-python
# covers the modern numerical-Python ecosystem (contourpy, numpy, scipy, matplotlib). meson
# and ninja are the actual build tools meson-python orchestrates; pyproject-metadata is a
# meson-python dependency.
_DEBUG_BUILD_REQUIRED_TOOLING = (
    "packaging",
    "setuptools",
    "wheel",
    "meson-python",
    "meson",
    "ninja",
    "pyproject-metadata",
    "cppy",
    "pybind11",
    "Cython",
    "pkgconf",
    "pythran",
    "setuptools_scm",
    "maturin",
)

# Packages with C extensions that pip must source-build against the debug Python rather
# than pull from PyPI as a wheel. Windows Py_DEBUG reports both cp3XXd and cp3XX as
# compatible platform tags, so without --no-binary pip happily picks a release wheel that
# then fails to load against python_d.exe at runtime. Names match pip's --no-binary syntax.
_DEBUG_BUILD_FROM_SOURCE = (
    "regex",
    "PyYAML",
    "httptools",
    "debugpy",
    "numpy",
    "scipy",
    "contourpy",
    "kiwisolver",
    "pillow",
    "matplotlib",
    "pydantic_core",
    "watchfiles",
    "lxml",
)

# Sitecustomize shim installed at <libpack>/bin/Lib/site-packages/sitecustomize.py for
# Debug LibPacks. Setuptools' build_ext defaults debug=False even when the target Python
# is Py_DEBUG, so source-built C extensions get the release CRT (/MD, VCRUNTIME140.dll)
# instead of the debug CRT (/MDd, VCRUNTIME140D.dll, ucrtbased.dll). The mismatch
# corrupts heap state in any extension that shares allocations across the C/Python
# boundary. This shim forces self.debug = True for every build_ext invocation when
# Py_DEBUG is detected via sysconfig. The unconditional warning at the bottom makes
# silent monkey-patch failure (for example a future setuptools rename) loud rather than
# silent.
_SITECUSTOMIZE_DEBUG_SHIM = '''\
"""Auto-loaded at interpreter startup by site.execsitecustomize().

When this Python is a Py_DEBUG build, force setuptools' build_ext to default
debug=True so MSVC compiles C extensions with /MDd (debug CRT) and links with
/DEBUG:FULL. Setuptools does not consult Py_DEBUG; without this shim, source-
built extensions get the release CRT and silently corrupt heap state in any
package that shares allocations across the C/Python boundary.

Installed by the FreeCAD LibPack build (compile_all.build_python, Debug mode)."""

import sys
import sysconfig

if sysconfig.get_config_var("Py_DEBUG"):
    _patched_build_ext = False
    _patched_msvc = False
    try:
        from setuptools.command.build_ext import build_ext as _build_ext
    except ImportError:
        _build_ext = None
    if _build_ext is not None:
        _orig_initialize_options = _build_ext.initialize_options

        def _initialize_options_force_debug(self):
            _orig_initialize_options(self)
            self.debug = True

        _build_ext.initialize_options = _initialize_options_force_debug
        _patched_build_ext = True

    try:
        from setuptools._distutils import _msvccompiler as _msvc
    except ImportError:
        try:
            import distutils._msvccompiler as _msvc
        except ImportError:
            _msvc = None
    if _msvc is not None:
        _orig_msvc_initialize = _msvc.MSVCCompiler.initialize

        def _initialize_with_fs(self, plat_name=None):
            _orig_msvc_initialize(self, plat_name)
            # Replace /Zi with /Z7 so debug info is embedded in each .obj rather than
            # written to a shared per-directory .pdb. /FS via mspdbsrv is the documented
            # fix for the parallel-build PDB race, but in pip-driven builds (notably
            # pillow's parallel-compile setup.py) it does not always reach all child
            # cl.exe instances. /Z7 sidesteps the race entirely by removing the shared
            # writer. The linker still produces a per-extension .pdb at link time.
            for options in (self.compile_options_debug, self.compile_options):
                while "/Zi" in options:
                    options[options.index("/Zi")] = "/Z7"
                if "/FS" not in options:
                    options.append("/FS")

        _msvc.MSVCCompiler.initialize = _initialize_with_fs
        _patched_msvc = True

    if not (_patched_build_ext and _patched_msvc):
        sys.stderr.write(
            "WARNING: FreeCAD LibPack sitecustomize could not fully patch setuptools/MSVC "
            "for Py_DEBUG compilation. C extensions built in this interpreter may use "
            "the release CRT or race on parallel PDB writes.\\n"
        )
'''


def _requirement_package_name(spec: str) -> str:
    """Extract the lowercased package name from a pip requirement specifier such as
    'numpy==2.4.4' or 'shapely==2.1.2; platform_machine != "ARM64"'."""
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", spec)
    return match.group(1).lower() if match else ""


class BuildMode(Enum):
    DEBUG = 1
    RELEASE = 2

    def __str__(self) -> str:
        if self == BuildMode.DEBUG:
            return "Debug"
        elif self == BuildMode.RELEASE:
            return "Release"
        else:
            return "Unknown"


def remove_readonly(func, path, _) -> None:
    """Remove a read-only file."""

    os.chmod(path, stat.S_IWRITE)
    func(path)


def patch_single_file(filename, patch_data) -> None:
    with open(filename, "r", encoding="utf-8") as f:
        original_data = f.read()
    dmp = diff_match_patch()
    patches = dmp.patch_fromText(patch_data)
    new_text, applied = dmp.patch_apply(patches, original_data)
    if not all(applied):
        print(f"ERROR: Failed to apply some patches to {filename}")
        # TODO: Someday actually print out what patches failed?
        exit(1)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(new_text)


def split_patch_data(patch_data: str) -> List[Dict[str, str]]:
    filename_regex = re.compile("@@@ ([^@]*) @@@\n")
    split_data = re.split(filename_regex, patch_data)
    result = []
    for index, entry in enumerate(split_data):
        if index == 0:
            if entry != "":
                print("ERROR: Bad patch file, must start with @@@ filename @@@")
                exit(1)
            continue
        if index % 2 == 1:
            result.append({"file": entry})
        else:
            result[-1]["data"] = entry
    return result


def apply_patch(patch_file_path: str) -> None:
    """Apply a patch that was generated by the generate_patch.py script"""
    # Path is relative to *this* file, not our working directory
    absolute_path = os.path.join(pathlib.Path(__file__).parent.absolute(), patch_file_path)
    with open(absolute_path, "r", encoding="utf-8") as f:
        patch_data = f.read()
    patches = split_patch_data(patch_data)
    for patch in patches:
        patch_single_file(patch["file"], patch["data"])


def patch_files(patches: List[str]) -> None:
    """Given a list of patches, apply them sequentially in the current working directory. The patches themselves are
    expected to be given as paths relative to **this** Python script file"""
    for patch in patches:
        start = len("patches/")
        print(f"  Applying patch {patch[start:]}")
        apply_patch(patch)


def libpack_arch_label() -> str:
    """Architecture suffix used in the LibPack directory and archive names.
    'x64' for Windows AMD64 and 'ARM64' for Windows on ARM, matching the
    convention used elsewhere in the build."""
    return "x64" if platform.machine() == "AMD64" else "ARM64"


def working_dir_name(mode: BuildMode) -> str:
    """Per-mode working directory name, allowing a Debug build and a Release build
    to coexist side-by-side instead of stomping on each other's clones and builds."""
    return "working-" + str(mode).lower()


def libpack_dir(config: dict, mode: BuildMode):
    lp_dir = "LibPack-{}-v{}-{}-{}".format(
        config["FreeCAD-version"],
        config["LibPack-version"],
        libpack_arch_label(),
        str(mode),
    )
    return os.path.join(os.path.dirname(__file__), working_dir_name(mode), lp_dir)


def to_exe(base: str = ""):
    """Append .exe to Windows executables, but not to macOS or Linux. If given no argument, just returns the extension
    for the current OS, suitable for appending to an executable's name."""
    return base + ".exe" if sys.platform.startswith("win32") else ""


def to_static(base: str = ""):
    """Append .lib to Windows libraries, or .a macOS or Linux. If given no argument, just returns the extension
    for the current OS, suitable for appending to a static library's name."""
    return base + ".lib" if sys.platform.startswith("win32") else ".a"


def to_dynamic(base: str = ""):
    """Append .dll to Windows libraries, or .so to macOS or Linux. If given no argument, just returns the extension
    for the current OS, suitable for appending to a dynamic library's name."""
    return base + ".dll" if sys.platform.startswith("win32") else ".so"


class Compiler:
    def __init__(
        self, config, bison_path, skip_existing: bool = False, mode: BuildMode = BuildMode.RELEASE
    ):
        self.config = config
        self.bison_path = bison_path
        self.base_dir = os.getcwd()
        self.skip_existing = skip_existing
        self.install_dir = libpack_dir(config, mode)
        self.init_script = None
        # Full MSVC tools version (for example "14.44.35207") to pass to MSBuild as
        # /p:VCToolsVersion. Required when the requested PlatformToolset (v143) lacks
        # a matching Microsoft.VCToolsVersion.v<N>.default.props file, in which case
        # MSBuild falls back to the newest installed compiler regardless of what the
        # environment or -vcvars_ver requested.
        self.msvc_tools_version = None
        self.mode = mode
        self.strict_mode = True

        # Right now there are two packages where the version number gets coded into the path when: Boost and Coin:
        # store those two separately from all the other paths we have to track
        self.boost_include_path = None
        self.coin_cmake_path = None

    def get_cmake_options(self) -> List[str]:
        """Get a comprehensive list of cMake options that can be used in any cMake build. Not all options apply
        to all builds, but none conflict."""
        pcre_lib = self.install_dir + "/lib/pcre2-8"
        if self.mode == BuildMode.DEBUG:
            pcre_lib += "d"
        pcre_lib += to_static()

        base = [
            "-D CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE",  # Never use system packages, always use only the libpack
            "-D CMAKE_FIND_PACKAGE_NO_SYSTEM_PACKAGE_REGISTRY=TRUE",  # Same as above?
            "-D CMAKE_CXX_STANDARD=20",
            f"-D BISON_EXECUTABLE={self.bison_path}",
            f"-D BOOST_ROOT={self.install_dir}",
            "-D BUILD_DOC=No",
            "-D BUILD_DOCS=No",
            "-D BUILD_EXAMPLES=No",
            "-D BUILD_SHARED=Yes",
            "-D BUILD_SHARED_LIB=Yes",
            "-D BUILD_SHARED_LIBS=Yes",
            "-D BUILD_TEST=No",
            "-D BUILD_TESTS=No",
            "-D BUILD_TESTING=No",
            f"-D BZIP2_DIR={self.install_dir}/lib/cmake/",
            f"-D Boost_INCLUDE_DIRS={self.install_dir}/include",
            f"-D CMAKE_BUILD_TYPE={self.mode}",
            f"-D CMAKE_INSTALL_PATH={self.install_dir}",
            f"-D CMAKE_INSTALL_PREFIX={self.install_dir}",
            f"-D HarfBuzz_DIR={self.install_dir}/lib/cmake/",
            f"-D HDF5_DIR={self.install_dir}/share/cmake/",
            f"-D HDF5_LIBRARY_DEBUG={self.install_dir}/lib/hdf5d.lib",
            f"-D HDF5_LIBRARY_RELEASE={self.install_dir}/lib/hdf5.lib",
            f"-D HDF5_DIFF_EXECUTABLE={self.install_dir}/bin/hdf5diff" + to_exe(),
            f"-D INSTALL_DIR={self.install_dir}",
            f"-D PCRE2_LIBRARY={pcre_lib}",
            "-D PIVY_USE_QT6=Yes",
            f"-D pybind11_DIR={self.install_dir}/share/cmake/pybind11",
            f"-D Python_ROOT_DIR={self.install_dir}/bin",
            f"-D Python_DIR={self.install_dir}/bin",
            f"-D Python3_ROOT_DIR={self.install_dir}/bin",
            f"-D Python3_DIR={self.install_dir}/bin",
            f"-D Python_EXECUTABLE={self.python_exe()}",
            f"-D Python3_EXECUTABLE={self.python_exe()}",
            "-D Python_FIND_REGISTRY=NEVER",
            "-D Python3_FIND_REGISTRY=NEVER",
            f"-D Qt6_DIR={self.install_dir}/lib/cmake/Qt6",
            f"-D SWIG_EXECUTABLE={self.install_dir}/bin/swig" + to_exe(),
            f"-D ZLIB_DIR={self.install_dir}/lib/cmake/",
            "-D CMAKE_DISABLE_FIND_PACKAGE_SoQt=True",
            # Absolutely never find SoQt (it's deprecated and we don't want it!)
        ]
        if self.mode == BuildMode.DEBUG:
            python_lib = self._python_lib_path()
            if python_lib:
                base.append(f"-D Python_LIBRARY={python_lib}")
                base.append(f"-D Python3_LIBRARY={python_lib}")
        if self.boost_include_path:
            base.append(f"-D Boost_INCLUDE_DIR={self.boost_include_path}")
        if self.coin_cmake_path:
            base.append(f"-D Coin_DIR={self.coin_cmake_path}")
        if sys.platform.startswith("win32"):
            inc_path = self.install_dir.replace("\\", "/")
            cxx_flags = (
                f"/I{inc_path}/include /EHsc /FS /DWIN32 /DWIN64 /DNOMINMAX /DPy_NO_LINK_LIB"
            )
            if self.strict_mode:
                # NOTE: /permissive- is required with Qt6 but could be disabled for anything that doesn't link against
                # Qt. The same is true for /Zc:__cplusplus /std:c++20
                cxx_flags += " /Zc:__cplusplus /std:c++20 /permissive-"
        else:
            cxx_flags = f"-I{self.install_dir}/include"
        base.append(f"-D CMAKE_CXX_FLAGS={cxx_flags}")
        return base

    def compile_all(self):
        # This option borks Tcl by making it find the wrong paths: remove it
        os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)
        for item in self.config["content"]:
            # All build methods are named using "build_XXX" where XXX is the name of the package in the config file
            os.chdir(item["name"])
            build_function_name = "build_" + item["name"]
            if hasattr(self, build_function_name):
                print(f"Building {item['name']}")
                build_function = getattr(self, build_function_name)
                build_function(item)
                if item["name"].lower() == "python":
                    # Check these even if we didn't actually have to build Python
                    self._build_pip()
                    if "requirements" in item:
                        self._install_python_requirements(item["requirements"])
            else:
                print(
                    f"No '{build_function_name}' found in compile_all.py -- "
                    "did you forget to add one when adding a dependency?"
                )
                exit(2)
            os.chdir(self.base_dir)

    def build_nonexistent(self, _=None):
        """Used for automated testing to allow easy Mock injection"""

    def build_libiconv(self, _=None):
        """Build win-iconv, a small Windows-targeted libiconv implementation. Provides
        iconv.lib for source-built lxml in Debug mode. Skipped entirely in Release mode
        because PyPI lxml wheels bundle their own iconv."""
        if self.mode != BuildMode.DEBUG:
            print("  Skipping libiconv build in Release mode (lxml wheel bundles its own iconv).")
            return
        if self.skip_existing:
            sentinel = os.path.join(self.install_dir, "include", "iconv.h")
            if os.path.exists(sentinel):
                print("  Not rebuilding libiconv, it is already in the LibPack")
                return
        extra_args = [
            "-G",
            "Ninja",
            "-D BUILD_SHARED_LIBS=ON",
            "-D BUILD_TEST=OFF",
            "-D CMAKE_POLICY_VERSION_MINIMUM=3.5",
        ]
        self._build_standard_cmake(extra_args=extra_args)

    def build_libxml2(self, _=None):
        """Build libxml2, providing the XML parser and tree API for source-built lxml in
        Debug mode. Skipped entirely in Release mode because PyPI lxml wheels bundle
        their own libxml2.

        Uses the Ninja CMake generator for the same reason as the other Debug-only C
        packages."""
        if self.mode != BuildMode.DEBUG:
            print("  Skipping libxml2 build in Release mode (lxml wheel bundles its own libxml2).")
            return
        if self.skip_existing:
            sentinel = os.path.join(
                self.install_dir, "include", "libxml2", "libxml", "xmlversion.h"
            )
            if os.path.exists(sentinel):
                print("  Not rebuilding libxml2, it is already in the LibPack")
                return
        extra_args = [
            "-G",
            "Ninja",
            "-D BUILD_SHARED_LIBS=ON",
            "-D LIBXML2_WITH_PYTHON=OFF",
            "-D LIBXML2_WITH_TESTS=OFF",
            "-D LIBXML2_WITH_ICONV=OFF",
            "-D LIBXML2_WITH_LZMA=OFF",
        ]
        self._build_standard_cmake(extra_args=extra_args)

    def build_libxslt(self, _=None):
        """Build libxslt, providing the XSLT engine for source-built lxml in Debug
        mode. Depends on libxml2 above. Skipped entirely in Release mode because PyPI
        lxml wheels bundle their own libxslt."""
        if self.mode != BuildMode.DEBUG:
            print("  Skipping libxslt build in Release mode (lxml wheel bundles its own libxslt).")
            return
        if self.skip_existing:
            sentinel = os.path.join(self.install_dir, "include", "libxslt", "xslt.h")
            if os.path.exists(sentinel):
                print("  Not rebuilding libxslt, it is already in the LibPack")
                return
        extra_args = [
            "-G",
            "Ninja",
            "-D BUILD_SHARED_LIBS=ON",
            "-D LIBXSLT_WITH_PYTHON=OFF",
            "-D LIBXSLT_WITH_TESTS=OFF",
        ]
        self._build_standard_cmake(extra_args=extra_args)

    def build_libjpeg(self, _=None):
        """Build libjpeg-turbo, providing the libjpeg API for source-built Pillow in
        Debug mode. Skipped entirely in Release mode because PyPI Pillow wheels bundle
        their own libjpeg, and nothing else in the Release LibPack consumes libjpeg.

        Uses the Ninja CMake generator for the same reason as OpenBLAS: it sidesteps
        the v143 PlatformToolset resolution failure that the default Visual Studio
        generator hits on VS 2026 installs missing the
        Microsoft.VCToolsVersion.v143.default.props file."""
        if self.mode != BuildMode.DEBUG:
            print(
                "  Skipping libjpeg-turbo build in Release mode (Pillow wheel bundles its own libjpeg)."
            )
            return
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "jpeglib.h")):
                print("  Not rebuilding libjpeg-turbo, it is already in the LibPack")
                return
        extra_args = [
            "-G",
            "Ninja",
            "-D ENABLE_SHARED=ON",
            "-D ENABLE_STATIC=OFF",
            "-D WITH_TURBOJPEG=ON",
            "-D BUILD_TESTING=OFF",
        ]
        self._build_standard_cmake(extra_args=extra_args)

    def build_openblas(self, _=None):
        """Build OpenBLAS, providing BLAS and LAPACK for source-built numpy and scipy
        in Debug mode. Skipped entirely in Release mode because PyPI numpy and scipy
        wheels bundle their own OpenBLAS in numpy/.libs/, and nothing else in the
        Release LibPack consumes BLAS.

        Uses the Ninja CMake generator rather than the default Visual Studio generator
        because (a) the VS generator does not handle Fortran well and OpenBLAS needs
        Flang for its Fortran sources, and (b) Ninja sidesteps an MSBuild
        PlatformToolset resolution failure on VS 2026 installs that ship the v143
        toolset without the matching Microsoft.VCToolsVersion.v143.default.props.
        Ninja must be on the build host PATH at the time this runs (typically via
        'pip install ninja' in the system Python, or a manual ninja.exe placement)."""
        if self.mode != BuildMode.DEBUG:
            print(
                "  Skipping OpenBLAS build in Release mode (numpy/scipy use bundled OpenBLAS from wheels)."
            )
            return
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "openblas", "cblas.h")):
                print("  Not rebuilding OpenBLAS, it is already in the LibPack")
                return
        extra_args = [
            "-G",
            "Ninja",
            "-D BUILD_SHARED_LIBS=ON",
            # DYNAMIC_ARCH=OFF: with DYNAMIC_ARCH=ON, kernel parameters like
            # GEMM_UNROLL_MN expand to runtime struct-pointer field accesses
            # (gotoblas -> ...). OpenBLAS uses those values as array sizes in C
            # files (driver/level3/zherk_kernel.c, others), which produces VLA
            # declarations that MSVC's C compiler does not support. In Release the
            # optimizer constant-folds them; in Debug /Od does not, and the build
            # fails. A single-arch build resolves the macros to compile-time
            # constants and side-steps the issue. Debug performance is not a target.
            "-D DYNAMIC_ARCH=OFF",
            "-D USE_THREAD=ON",
            "-D NUM_THREADS=64",
            "-D BUILD_WITHOUT_LAPACK=OFF",
            "-D NOFORTRAN=OFF",
            "-D BUILD_TESTING=OFF",
            "-D CMAKE_POLICY_VERSION_MINIMUM=3.5",
        ]
        self._build_standard_cmake(extra_args=extra_args)

    def python_exe(self):
        if self.mode == BuildMode.RELEASE:
            return os.path.join(self.install_dir, "bin", "python") + to_exe()
        return os.path.join(self.install_dir, "bin", "python_d") + to_exe()

    def _python_lib_path(self) -> Optional[str]:
        """Locate the versioned Python import library in the LibPack libs directory,
        for example python314.lib (release) or python314_d.lib (debug). Returns None
        if the libs directory or matching file does not yet exist, which is expected
        before build_python has run."""
        libs_dir = os.path.join(self.install_dir, "bin", "libs")
        if not os.path.isdir(libs_dir):
            return None
        suffix = "_d" if self.mode == BuildMode.DEBUG else ""
        pattern = re.compile(rf"^python\d{{2,}}{re.escape(suffix)}\.lib$")
        for name in os.listdir(libs_dir):
            if pattern.match(name):
                return os.path.join(libs_dir, name)
        return None

    def _python_build_env(self):
        """Environment for the host Python that PCbuild\\build.bat -e launches to fetch
        external sources. Some Python installs on Windows ship without a usable CA bundle,
        which makes get_external.py fail with SSL: CERTIFICATE_VERIFY_FAILED when
        downloading from GitHub. Point SSL_CERT_FILE at the certifi bundle that ships with
        requests so the host Python can verify TLS. Honor any value the caller already
        set."""
        env = os.environ.copy()
        if "SSL_CERT_FILE" not in env:
            try:
                import certifi

                ca_bundle = certifi.where()
            except ImportError:
                ca_bundle = None
            if ca_bundle and os.path.exists(ca_bundle):
                env["SSL_CERT_FILE"] = ca_bundle
        return env

    def build_python(self, args=None):
        if self.skip_existing:
            if os.path.exists(self.python_exe()):
                print("  Not rebuilding Python, it is already in the LibPack")
                return
        if sys.platform.startswith("win32"):
            expected_exe_path = self.python_exe()
            arch = "x64" if platform.machine() == "AMD64" else "ARM64"
            path = "amd64" if platform.machine() == "AMD64" else "arm64"
            env = self._python_build_env()
            # When MSBuild's PlatformToolset selection chain cannot resolve a default
            # VCToolsVersion for v143 (the case on Visual Studio 2026 installs that
            # ship the v143 toolset but not Microsoft.VCToolsVersion.v143.default.props),
            # MSBuild silently picks the newest installed compiler and then fails the
            # toolset compatibility check. Force the version via PCbuild\\msbuild.rsp,
            # which build.bat documents as the supported way to inject extra MSBuild
            # flags. Command-line /p: cannot be used here because cmd's batch parameter
            # parser splits on the '=' before build.bat passes %1..%9 through.
            rsp_path = pathlib.Path("PCbuild") / "msbuild.rsp"
            if self.msvc_tools_version:
                rsp_path.write_text(
                    f"/p:VCToolsVersion={self.msvc_tools_version}\n", encoding="utf-8"
                )
            try:
                self._run_streaming(
                    [
                        *self.init_script,
                        "&",
                        "PCbuild\\build.bat",
                        "-p",
                        arch,
                        "-c",
                        str(self.mode),
                        "-e",
                    ],
                    "build_log.txt",
                    env=env,
                )
            except subprocess.CalledProcessError as e:
                print("Python build failed")
                if e.output:
                    print(e.output.decode("utf-8", errors="replace"))
                exit(e.returncode)
            except FileNotFoundError as e:
                print(f"Could not find file: {e}")
                exit(-1)
            bin_dir = os.path.join(self.install_dir, "bin")
            dll_dir = os.path.join(bin_dir, "DLLs")
            lib_dir = os.path.join(bin_dir, "Lib")
            libs_dir = os.path.join(bin_dir, "libs")
            inc_dir = os.path.join(bin_dir, "Include")
            tools_dir = os.path.join(bin_dir, "Tools")
            os.makedirs(bin_dir, exist_ok=True)
            os.makedirs(dll_dir, exist_ok=True)
            os.makedirs(lib_dir, exist_ok=True)
            os.makedirs(libs_dir, exist_ok=True)
            os.makedirs(bin_dir, exist_ok=True)
            os.makedirs(tools_dir, exist_ok=True)
            tools_subs = ["i18n", "scripts"]
            for sub in tools_subs:
                os.makedirs(os.path.join(tools_dir, sub), exist_ok=True)

            # NOTES:
            # When installed via the Python installer, the top-level Python folder contains:
            #   python.exe
            #   python.pdb
            #   python3.dll
            #   python3xx.dll
            #   python3xx.pdb
            #   python3xx_d.dll
            #   python3xx_d.pdb
            #   python3_d.dll
            #   pythonw.exe
            #   pythonw.pdb
            #   pythonw_d.exe
            #   pythonw_d.pdb
            #   python_d.exe
            #   python_d.pdb
            #   vcruntime140.dll
            #   vcruntime140_1.dll
            # It also contains 5 subdirectories: DLLs, include, Lib, libs, and Tools, plus LICENSE.txt
            #    DLLS folder contains *.pyd, *.pdb, and *.dll
            #    include contains the header file directory tree
            #    Lib contains the Python standard libraries
            #    libs contains the actual Python *.lib files (python3.lib and python3xx.lib and their debug equivalents
            #    Tools contains a number of subdirectories with Python scripts: i18n, scripts, and demo
            # Finally, we also need the file "pyconfig.h" which is in yet another directory of the Python build, "PC"

            shutil.copytree(f"PCBuild\\{path}", dll_dir, dirs_exist_ok=True)
            shutil.copytree(f"Lib", lib_dir, dirs_exist_ok=True)
            shutil.copytree(f"Include", inc_dir, dirs_exist_ok=True)
            for sub in tools_subs:
                shutil.copytree(f"Tools\\{sub}", os.path.join(tools_dir, sub), dirs_exist_ok=True)

            # Figure out what version of Python we just built:
            exe_name = "python.exe" if self.mode == BuildMode.RELEASE else "python_d.exe"
            major, minor = self.get_python_version(os.path.join("PCBuild", path, exe_name)).split(
                "."
            )

            # Construct the list of files we expect to exist that need to be placed in the toplevel directory, or in
            # libs:
            move_to_bin = ["vcruntime"]
            for base in ["python", f"python{major}", f"python{major}{minor}", "pythonw"]:
                final = base
                if self.mode == BuildMode.DEBUG:
                    final += "_d"
                move_to_bin.append(final)
            # They are all in the DLLs subdirectory now: move the ones that match:
            for file in pathlib.Path(dll_dir).iterdir():
                if file.is_file():
                    if file.stem in move_to_bin:
                        if file.suffix == ".lib":
                            target = os.path.join(libs_dir, file.name)
                        elif file.suffix in [".dll", ".exe", ".pdb"]:
                            target = os.path.join(bin_dir, file.name)
                        else:
                            continue
                        if os.path.exists(target):
                            os.unlink(target)
                        file.rename(target)
            pyconfig = os.path.join("PC", "pyconfig.h")
            target = os.path.join(inc_dir, "pyconfig.h")
            if not os.path.exists(pyconfig):
                print("ERROR: Could not locate pyconfig.h, cannot complete installation of Python")
                exit(1)
            if os.path.exists(target):
                os.unlink(target)
            print(f"Copying {pyconfig} to {target}")
            shutil.copyfile(pyconfig, target)
            if self.mode == BuildMode.DEBUG:
                # FindPython on Windows searches for the release-named library and runtime
                # DLL independently of any debug-variant hint. Without same-named files in
                # the LibPack the search escapes to a system Python install and downstream
                # find_dependency(Python COMPONENTS Development) calls (boost_python's
                # installed config) reject Development.Embed because the debug library and
                # release runtime resolve to different installs. Same-content release-named
                # copies keep every component lookup inside the LibPack.
                versioned = f"python{major}{minor}"
                shutil.copy(
                    os.path.join(libs_dir, f"{versioned}_d.lib"),
                    os.path.join(libs_dir, f"{versioned}.lib"),
                )
                shutil.copy(
                    os.path.join(bin_dir, f"{versioned}_d.dll"),
                    os.path.join(bin_dir, f"{versioned}.dll"),
                )
                # FreeCAD's CMake (and CMake's own FindPython) searches for python.exe
                # and pythonw.exe by their release names. Provide same-content copies
                # next to the debug-suffixed originals so downstream consumers find a
                # Python executable inside the LibPack instead of escaping to a system
                # install with a different ABI.
                for exe_pair in (("python_d.exe", "python.exe"), ("pythonw_d.exe", "pythonw.exe")):
                    src = os.path.join(bin_dir, exe_pair[0])
                    dst = os.path.join(bin_dir, exe_pair[1])
                    if os.path.exists(src):
                        shutil.copy(src, dst)
                # Python's installed import libraries live at <install>/bin/libs/, the
                # location FindPython expects. However, anything that transitively
                # includes Python.h triggers `#pragma comment(lib, "pythonXY_d.lib")`,
                # and that auto-link only finds the file when the linker's search path
                # already covers <install>/bin/libs/. Downstream consumers (such as
                # FreeCAD's own CMake) typically only add <install>/lib/ to the linker
                # search path. Mirror both libs into <install>/lib/ so the auto-link
                # resolves without requiring downstream configuration changes.
                top_lib_dir = os.path.join(self.install_dir, "lib")
                for lib_name in (f"{versioned}_d.lib", f"{versioned}.lib"):
                    src = os.path.join(libs_dir, lib_name)
                    if os.path.exists(src):
                        shutil.copy(src, os.path.join(top_lib_dir, lib_name))
                site_packages_dir = os.path.join(lib_dir, "site-packages")
                os.makedirs(site_packages_dir, exist_ok=True)
                with open(
                    os.path.join(site_packages_dir, "sitecustomize.py"),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(_SITECUSTOMIZE_DEBUG_SHIM)
        else:
            raise NotImplemented("Non-Windows compilation of Python is not implemented yet")

    def get_python_version(self, exe: str = None) -> str:
        if exe is None:
            path_to_python = self.python_exe()
        else:
            path_to_python = exe
        try:
            result = subprocess.run([path_to_python, "--version"], capture_output=True, check=True)
            _, _, version_number = result.stdout.decode("utf-8").strip().partition(" ")
            components = version_number.split(".")
            python_version = f"{components[0]}.{components[1]}"
            return python_version
        except subprocess.CalledProcessError as e:
            print("ERROR: Failed to run LibPack's Python executable")
            print(e.stdout.decode("utf-8"))
            if e.stderr:
                print(e.stderr.decode("utf-8"))
            exit(1)

    def _build_pip(self, _=None):
        print("  Installing the latest pip")
        path_to_python = self.python_exe()
        try:
            self._run_streaming([path_to_python, "-m", "ensurepip", "--upgrade"], "pip_log.txt")
            self._run_streaming(
                [path_to_python, "-m", "pip", "install", "--upgrade", "pip"], "pip_log.txt"
            )
        except subprocess.CalledProcessError as e:
            print("ERROR: Failed to run LibPack's Python executable")
            if e.output:
                print(e.output.decode("utf-8", errors="replace"))
            exit(1)

    def _filter_debug_requirements(self, requirements):
        kept = [
            spec
            for spec in requirements
            if _requirement_package_name(spec) not in _DEBUG_BUILD_EXCLUDED_REQUIREMENTS
        ]
        kept_names = {_requirement_package_name(spec) for spec in kept}
        for tool in _DEBUG_BUILD_REQUIRED_TOOLING:
            if tool.lower() not in kept_names:
                kept.append(tool)
        return kept

    def _install_debug_library_aliases(self):
        """Create release-named copies of debug-suffixed import libraries so that
        source-built Python C extensions can find them under their conventional names.
        Pillow's setup.py looks for 'zlib' and 'libpng16' literally, ignoring CMake's
        'd' debug suffix; numpy and scipy search for BLAS by similarly fixed names.
        This is a flat list of known-needed aliases rather than a heuristic sweep,
        because some legitimate library names happen to end in 'd' for unrelated
        reasons. Aliases are only created when both the debug source exists and the
        release target does not."""
        if self.mode != BuildMode.DEBUG:
            return
        aliases = (
            ("lib/zd.lib", "lib/zlib.lib"),
            ("lib/libpng16d.lib", "lib/libpng16.lib"),
            ("lib/libxml2d.lib", "lib/libxml2.lib"),
            ("lib/libxsltd.lib", "lib/libxslt.lib"),
            ("lib/libexsltd.lib", "lib/libexslt.lib"),
        )
        for src, dst in aliases:
            src_path = os.path.join(self.install_dir, src)
            dst_path = os.path.join(self.install_dir, dst)
            if os.path.exists(src_path) and not os.path.exists(dst_path):
                shutil.copy(src_path, dst_path)

    def _install_python_requirements(self, requirements):
        if self.mode == BuildMode.DEBUG:
            requirements = self._filter_debug_requirements(requirements)
        sentinel = "packaging" if self.mode == BuildMode.DEBUG else "PIL"
        if self.skip_existing:
            if os.path.exists(
                os.path.join(self.install_dir, "bin", "Lib", "site-packages", sentinel)
            ):
                print("  Not re-installing Python requirements, they are already in the LibPack")
                return
        if self.mode == BuildMode.DEBUG:
            # The main install below uses --no-build-isolation, which requires PEP 517
            # build backends (setuptools, meson-python, etc.) to already be present in the
            # LibPack environment. Pip's resolver is single-pass: it cannot install a
            # backend in the same install request that needs the backend to fetch metadata
            # for some other package. Bootstrap the tooling first via a separate pip call
            # with normal isolated builds (the tooling itself is pure-Python or binary, so
            # isolation is harmless there).
            print("  Installing build-time tooling")
            self._run_pip_install(
                list(_DEBUG_BUILD_REQUIRED_TOOLING),
                no_build_isolation=False,
                no_binary_packages=(),
            )
            self._install_debug_library_aliases()
        print("  Installing the following requirements (and their dependencies) using pip:")
        for req in requirements:
            print("    " + req)
        # meson-python defaults to "-Dbuildtype=release -Db_ndebug=if-release -Db_vscrt=md"
        # regardless of the target Python's debug-ness. b_vscrt=md forces /MD (release
        # CRT) independently of buildtype, so overriding only buildtype leaves extensions
        # linked against VCRUNTIME140.dll. Pass both -Dbuildtype=debug (so meson selects
        # debug compile flags and no NDEBUG) and -Db_vscrt=mdd (so the linker uses
        # ucrtbased.dll and VCRUNTIME140D.dll).
        # No explicit blas / lapack option here: numpy and scipy auto-detect OpenBLAS
        # via the openblas.pc that pkg-config sees through PKG_CONFIG_PATH set in the
        # subprocess env below. Most other meson-python projects (contourpy, etc.) do
        # not declare a blas option and would error if we passed one.
        config_settings = (
            (
                ("setup-args", "-Dbuildtype=debug"),
                ("setup-args", "-Db_vscrt=mdd"),
                # cpp_std=c++17 is required for pythran-generated C++ in scipy. Pythran's
                # generated headers still use std::result_of_t, which C++20 removed.
                # MSVC's default standard is newer than C++17 in current toolsets, so we
                # pin it explicitly. Numpy's own meson.build already pins c++17, so this
                # change is a no-op for numpy and a fix for scipy.
                ("setup-args", "-Dcpp_std=c++17"),
            )
            if self.mode == BuildMode.DEBUG
            else ()
        )
        # Scipy needs an extra meson option that other meson-python projects (numpy in
        # particular) reject as unknown. Pull it out for a separate pip pass.
        scipy_specs: list = []
        if self.mode == BuildMode.DEBUG:
            scipy_specs = [r for r in requirements if _requirement_package_name(r) == "scipy"]
            if scipy_specs:
                requirements = [r for r in requirements if _requirement_package_name(r) != "scipy"]
        self._run_pip_install(
            requirements,
            no_build_isolation=(self.mode == BuildMode.DEBUG),
            no_binary_packages=(_DEBUG_BUILD_FROM_SOURCE if self.mode == BuildMode.DEBUG else ()),
            config_settings=config_settings,
        )
        if scipy_specs:
            print("  Installing scipy with use-pythran=false")
            self._run_pip_install(
                scipy_specs,
                no_build_isolation=True,
                no_binary_packages=("scipy",),
                # Pythran 0.18 headers fail to compile under MSVC for scipy's
                # pythran-translated modules (a ref-qualifier overload mismatch in
                # ndarray.hpp). Disabling pythran skips those modules; scipy provides
                # pure-Python fallbacks for each.
                config_settings=config_settings + (("setup-args", "-Duse-pythran=false"),),
            )

    def _run_pip_install(
        self, requirements, no_build_isolation, no_binary_packages, config_settings=()
    ):
        path_to_python = self.python_exe()
        pip_args = [
            path_to_python,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--ignore-installed",
            "--no-warn-script-location",
        ]
        if no_build_isolation:
            pip_args.append("--no-build-isolation")
        for pkg in no_binary_packages:
            pip_args.extend(["--no-binary", pkg])
        for key, value in config_settings:
            pip_args.append(f"--config-settings={key}={value}")
        pip_args.extend(requirements)
        if self.mode == BuildMode.DEBUG:
            # Source-built C extensions need MSVC visible. Source vcvars first and tell
            # setuptools to trust the existing SDK env instead of auto-detecting compilers.
            # Also expose the LibPack's Scripts directory on PATH so build backends like
            # meson-python can find the meson and ninja executables that were installed
            # alongside their Python packages, and prepend the LibPack's include and lib
            # directories to INCLUDE and LIB so packages built against LibPack-bundled
            # C/C++ libraries (pillow against libpng, zlib, freetype, for example) find
            # their headers and import libs.
            env = os.environ.copy()
            env["DISTUTILS_USE_SDK"] = "1"
            env["MSSdk"] = "1"
            # kiwisolver's pyproject.toml declares dynamic version via setuptools_scm,
            # which falls back to "0.0.0" outside a git checkout. Pip's metadata
            # consistency check then rejects the sdist (requested 1.5.0, got 0.0.0).
            # The setuptools_scm-native override is package-scoped by name suffix,
            # so it does not affect any other setuptools_scm packages we install.
            env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_KIWISOLVER"] = "1.5.0"
            # lxml on Windows defaults to STATIC_DEPS=true, which downloads
            # libxml2/libxslt sources and bundles them statically. We provide both
            # as LibPack packages with pkg-config files (libxml-2.0.pc, libxslt.pc).
            # STATIC_DEPS=false switches lxml to the system-deps path; pkg-config
            # then supplies the correct -I${includedir}/libxml2 cflag that lxml's
            # source needs to find <libxml/xmlversion.h>.
            env["STATIC_DEPS"] = "false"
            # pkgconf-pypi 2.5.x has a bug in its pkg-config.exe wrapper: when
            # PKG_CONFIG_PATH is set in the environment, the wrapper's
            # _vanilla_entrypoint never invokes the bundled pkgconf binary at all
            # and exits 0 with no output. FORCE_PKGCONF_PYPI=1 routes the wrapper
            # through _python_aware_entrypoint which calls pkgconf correctly. lxml
            # is the first package whose setup.py invokes pkg-config at build time;
            # without this, pkg-config silently returns no flags and lxml's compile
            # cannot find <libxml/xmlversion.h>.
            env["FORCE_PKGCONF_PYPI"] = "1"
            scripts_dir = os.path.join(self.install_dir, "bin", "Scripts")
            bin_dir = os.path.join(self.install_dir, "bin")
            env["PATH"] = scripts_dir + os.pathsep + bin_dir + os.pathsep + env.get("PATH", "")
            include_dir = os.path.join(self.install_dir, "include")
            python_include_dir = os.path.join(self.install_dir, "bin", "Include")
            lib_dir = os.path.join(self.install_dir, "lib")
            python_lib_dir = os.path.join(self.install_dir, "bin", "libs")
            env["INCLUDE"] = (
                include_dir + os.pathsep + python_include_dir + os.pathsep + env.get("INCLUDE", "")
            )
            env["LIB"] = lib_dir + os.pathsep + python_lib_dir + os.pathsep + env.get("LIB", "")
            # Tell pkg-config and CMake where to find LibPack-installed packages so meson's
            # dependency() resolves OpenBLAS (and any future LibPack-bundled lib) by either
            # method. pkg-config is the preferred lookup for numpy and scipy; CMake is the
            # fallback meson tries when pkg-config does not find a match.
            pkgconfig_lib_dir = os.path.join(self.install_dir, "lib", "pkgconfig")
            pkgconfig_share_dir = os.path.join(self.install_dir, "share", "pkgconfig")
            env["PKG_CONFIG_PATH"] = (
                pkgconfig_lib_dir
                + os.pathsep
                + pkgconfig_share_dir
                + os.pathsep
                + env.get("PKG_CONFIG_PATH", "")
            )
            env["CMAKE_PREFIX_PATH"] = (
                self.install_dir + os.pathsep + env.get("CMAKE_PREFIX_PATH", "")
            )
            if platform.machine() == "ARM64":
                # MSVC's ARM64 linker mitigates Cortex-A53 erratum #843419 by inserting
                # padding NOPs, which only works when each function lives in its own
                # COMDAT. /Gy enables that layout. Release builds get /Gy implicitly.
                existing_cl = env.get("CL", "").strip()
                env["CL"] = ("/Gy " + existing_cl).strip() if existing_cl else "/Gy"
            call_args = [*self.init_script, "&", *pip_args]
        else:
            env = None
            call_args = pip_args
        try:
            self._run_streaming(call_args, "pip_log.txt", env=env)
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to pip install requirements")
            if e.output:
                print(e.output.decode("utf-8", errors="replace"))
            exit(1)

    def build_opengl32sw(self, _: None):
        """Copy Mesa software OpenGL DLL into the LibPack on x64. On ARM64 the DLL is
        unavailable from Qt's CDN. Does not actually build anything, just copies it."""
        target = os.path.join(self.install_dir, "bin", "opengl32sw.dll")
        if self.skip_existing and os.path.exists(target):
            print("  opengl32sw.dll already in the LibPack, not re-copying")
            return
        if platform.machine() == "ARM64":
            print("  NOTE: opengl32sw.dll is not available for Windows on ARM64 - Skipping")
            return
        matches = list(pathlib.Path(os.getcwd()).rglob("opengl32sw.dll"))
        if not matches:
            print(
                f"ERROR: opengl32sw.dll not found under {os.getcwd()}. "
                "The download or 7z extraction probably failed; check the URL "
                "in config.json and retry."
            )
            exit(1)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(str(matches[0]), target)
        print(f"  Copied {matches[0]} to {target}")

    def build_qt(self, options: dict):
        """Build Qt from source. Always builds qtbase, qtsvg, qtdeclarative, and qttools
        against the LibPack's own zlib and libpng."""
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "metatypes")):
                print("  Not rebuilding Qt, it is already in the LibPack")
                return
        self._prepend_debug_crt_to_path()

        build_dir = os.path.join(os.getcwd(), f"build-{str(self.mode).lower()}")
        if len(build_dir) > 20:
            print(
                "  WARNING: Qt uses incredibly long path names which end up right at the very edge of what\n"
                "  can be supported. In order to build successfully it might be necessary to use a very short\n"
                '  path name for the actual build directory (e.g., "C:\\temp").\n'
            )
            if "fallback-build-dir" in options:
                print(f"  Using fallback build directory {options['fallback-build-dir']}")
                build_dir = options["fallback-build-dir"]
            else:
                print(
                    f"  Attempting to use default path {build_dir}. \n\nIf the build fails, consider making a temp directory to work in.\n"
                )

        os.makedirs(build_dir, exist_ok=True)
        old_cwd = os.getcwd()
        os.chdir(build_dir)

        # Qt needs access to zlib and libpng, and assumes they are installed at the system level. We want to
        # use the LibPack versions. The easiest thing to do is just copy the DLLs:
        if self.mode == BuildMode.DEBUG:
            files = ["zd.dll", "libpng16d.dll"]
        else:
            files = ["z.dll", "libpng16.dll"]
        source = os.path.join(self.install_dir, "bin")
        destination = os.path.join(build_dir, "qtbase", "bin")
        os.makedirs(destination, exist_ok=True)
        for f in files:
            shutil.copy(os.path.join(source, f), destination)

        submodules = ["qtbase", "qtsvg", "qtdeclarative", "qttools", "qtremoteobjects"]
        init_command = [
            *self.init_script,
            "&",
            os.path.join(old_cwd, "configure.bat"),
            "-opensource",
            "-init-submodules",
            "-submodules",
            ",".join(submodules),
            "-feature-opengl",
            "-prefix",
            self.install_dir,
            "-opengl",
            "desktop",
        ]
        if self.mode == BuildMode.DEBUG:
            init_command.append("-debug")
        try:
            self._run_streaming(init_command, "configure_log.txt")
        except subprocess.CalledProcessError as e:
            print("ERROR: Qt configure failed!")
            print(f"Command: {' '.join(init_command)}")
            if e.output:
                print(e.output.decode("utf-8", errors="replace"))
            exit(e.returncode)

        self._cmake_build()
        self._cmake_install()
        os.chdir(old_cwd)

    def build_boost(self, _=None):
        if self.skip_existing:
            start_crawl_at = os.path.join(self.install_dir, "include")
            contents = [
                f
                for f in os.listdir(start_crawl_at)
                if os.path.isdir(os.path.join(start_crawl_at, f))
            ]
            for item in contents:
                if item.startswith("boost"):
                    print("  Not rebuilding boost, it is already in the LibPack")
                    return
        extra_args = [
            "-D BOOST_INSTALL_LAYOUT=versioned",
            "-D BOOST_ENABLE_CMAKE=ON",
            "-D BOOST_EXCLUDE_LIBRARIES='mpi;graph_parallel;coroutine'",
            "-D BOOST_ENABLE_PYTHON=ON",
            "-D BOOST_LOCALE_ENABLE_ICU=OFF",
        ]
        if platform.machine() == "ARM64" and sys.platform == "win32":
            print(
                "  (NOTE: For Windows-on-ARM, Boost is being configured to use Windows Fibers in boost::context)"
            )
            extra_args.append("-D BOOST_CONTEXT_IMPLEMENTATION=winfib")
        self._build_standard_cmake(extra_args)
        self._configure_boost_version()

    def _configure_boost_version(self):
        """Once Boost has been installed, figure out what version it was and set up the correct include path"""
        start_crawl_at = os.path.join(self.install_dir, "include")
        contents = [
            f for f in os.listdir(start_crawl_at) if os.path.isdir(os.path.join(start_crawl_at, f))
        ]
        for item in contents:
            if item.startswith("boost"):
                self.boost_include_path = os.path.join(start_crawl_at, item)
                break

    def _cmake_create_build_dir(self):
        build_dir = "build-" + str(self.mode).lower()
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir, onerror=remove_readonly)
        os.mkdir(build_dir)
        os.chdir(build_dir)

    def _run_streaming(self, args, log_filename: str = "build_log.txt", env=None):
        """Run a subprocess and stream its combined stdout and stderr to log_filename one
        line at a time. The log file is opened in append mode and line-buffered so an
        external watcher can tail it in real time. The output of this invocation is also
        retained in memory so that, on a non-zero exit, it can be attached to the raised
        CalledProcessError exactly as subprocess.run would have done."""
        captured_lines: List[str] = []
        with open(log_filename, "a", encoding="utf-8", buffering=1) as logf:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            for line in proc.stdout:
                logf.write(line)
                captured_lines.append(line)
            return_code = proc.wait()
        if return_code != 0:
            raise subprocess.CalledProcessError(
                return_code, args, output="".join(captured_lines).encode("utf-8")
            )

    def _run_cmake(self, args):
        cmake_setup_options = [*self.init_script, "&", "cmake"]
        cmake_setup_options.extend(args)
        try:
            self._run_streaming(cmake_setup_options, "build_log.txt")
        except subprocess.CalledProcessError as e:
            print("ERROR: cMake failed!")
            print(f"Command: {' '.join(cmake_setup_options)}")
            if e.output:
                print(e.output.decode("utf-8", errors="replace"))
            exit(e.returncode)

    def _prepend_debug_crt_to_path(self) -> None:
        """Make the LibPack's debug DLLs (Qt's zd.dll, libpng16d.dll, and similar) and
        the MSVC and Universal CRT debug DLLs (msvcp140d.dll, vcruntime140d.dll,
        ucrtbased.dll) discoverable by freshly-built debug tools"""
        if self.mode != BuildMode.DEBUG or sys.platform != "win32":
            return
        arch_lower = "arm64" if platform.machine() == "ARM64" else "x64"
        toolset_prefix = ""
        if self.msvc_tools_version:
            toolset_prefix = ".".join(self.msvc_tools_version.split(".")[:2])
        extra_dirs: List[str] = [os.path.join(self.install_dir, "bin")]
        vs_root = pathlib.Path("C:/Program Files/Microsoft Visual Studio")
        if vs_root.is_dir():
            for vs_major in sorted(vs_root.iterdir(), reverse=True):
                redist = vs_major / "Community" / "VC" / "Redist" / "MSVC"
                if not redist.is_dir():
                    continue
                candidates = [d for d in redist.iterdir() if d.is_dir()]
                if toolset_prefix:
                    matching = [d for d in candidates if d.name.startswith(toolset_prefix + ".")]
                    if matching:
                        candidates = matching
                candidates.sort(
                    key=lambda d: tuple(int(p) for p in d.name.split(".") if p.isdigit()),
                    reverse=True,
                )
                for ver in candidates:
                    for crt_dir in ver.glob(f"debug_nonredist/{arch_lower}/Microsoft.VC*.DebugCRT"):
                        if (crt_dir / "vcruntime140d.dll").exists():
                            extra_dirs.append(str(crt_dir))
                            break
                    if len(extra_dirs) > 1:
                        break
                if len(extra_dirs) > 1:
                    break
        sdk_bin = pathlib.Path("C:/Program Files (x86)/Windows Kits/10/bin")
        if sdk_bin.is_dir():
            sdk_versions = sorted(
                [d for d in sdk_bin.iterdir() if d.is_dir() and re.match(r"^\d+\.", d.name)],
                key=lambda d: tuple(int(p) for p in d.name.split(".") if p.isdigit()),
                reverse=True,
            )
            for ver in sdk_versions:
                ucrt = ver / arch_lower / "ucrt"
                if (ucrt / "ucrtbased.dll").exists():
                    extra_dirs.append(str(ucrt))
                    break
        current_path = os.environ.get("PATH", "")
        path_parts = current_path.split(os.pathsep) if current_path else []
        normalized = {os.path.normcase(p) for p in path_parts}
        prepend = [d for d in extra_dirs if os.path.normcase(d) not in normalized]
        if prepend:
            os.environ["PATH"] = os.pathsep.join(prepend + path_parts)

    def _arm64_platform_flag(self, generator_args: List[str]) -> List[str]:
        """Return ['-A ARM64'] when the active CMake generator accepts a platform
        selector."""
        if not (sys.platform.startswith("win32") and platform.machine() == "ARM64"):
            return []
        args = generator_args or []
        for index, arg in enumerate(args):
            if arg == "-G" and index + 1 < len(args):
                return ["-A ARM64"] if "Visual Studio" in args[index + 1] else []
            if arg.startswith("-G") and arg != "-G":
                generator = arg[2:].lstrip("=").strip()
                return ["-A ARM64"] if "Visual Studio" in generator else []
        return ["-A ARM64"]

    def _cmake_configure(self, extra_args: List[str] = None):
        options = self.get_cmake_options()
        if extra_args:
            options.extend(extra_args)
        options.extend(self._arm64_platform_flag(extra_args))
        options.append(
            ".."
        )  # Because the source code is located one directory up from our build location
        self._run_cmake(options)

    def _cmake_build(self, parallel: bool = True):
        cmake_build_options = ["--build", ".", "--config", str(self.mode).lower(), "--verbose"]
        if parallel:
            cmake_build_options.append("--parallel")
        self._run_cmake(cmake_build_options)

    def _cmake_install(self):
        cmake_install_options = ["--install", ".", "--config", str(self.mode).lower()]
        self._run_cmake(cmake_install_options)

    def _build_standard_cmake(self, extra_args: List[str] = None):
        self._cmake_create_build_dir()
        self._cmake_configure(extra_args)
        self._cmake_build()
        self._cmake_install()

    def _pip_install(self, requirement: str) -> None:
        path_to_python = self.python_exe()
        package_name = requirement.split("==")[0]
        try:
            self._run_streaming(
                [path_to_python, "-m", "pip", "uninstall", "--yes", package_name],
                "pip_log.txt",
            )
        except subprocess.CalledProcessError as e:
            print(f"{package_name} was not uninstalled... continuing")
            pass
        try:
            self._run_streaming(
                [path_to_python, "-m", "pip", "install", "--ignore-installed", requirement],
                "pip_log.txt",
            )
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to pip install {requirement}")
            if e.output:
                print(e.output.decode("utf-8", errors="replace"))
            exit(1)

    def _build_with_pip(self, options: dict):
        if "pip-install" not in options:
            print(
                f"ERROR: No pip-install provided in config of {options['name']}, so version cannot be determined"
            )
            exit(1)
        self._pip_install(options["pip-install"])

    def build_coin(self, _=None):
        """Builds and installs Coin using standard CMake settings"""
        if self.skip_existing:
            self._configure_coin_cmake_path()
            if self.coin_cmake_path is not None:
                print("  Not rebuilding Coin, it is already in the LibPack")
                return
        extra_args = ["-D COIN_BUILD_TESTS=Off"]
        self._build_standard_cmake(extra_args)
        self._configure_coin_cmake_path()

    def _configure_coin_cmake_path(self):
        """Coin installs its cMake file into a directory named with the full version, so figure out what that is"""
        start_crawl_at = os.path.join(self.install_dir, "lib", "cmake")
        contents = [
            f for f in os.listdir(start_crawl_at) if os.path.isdir(os.path.join(start_crawl_at, f))
        ]
        for item in contents:
            if item.startswith("Coin"):
                self.coin_cmake_path = os.path.join(start_crawl_at, item)
                break

    def build_quarter(self, _=None):
        """Builds and installs Quarter using standard CMake settings"""
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "Quarter")):
                print("  Not rebuilding Quarter, it is already in the LibPack")
                return
        extra_args = [
            "-D QUARTER_BUILD_EXAMPLES=Off",
            "-D QUARTER_USE_QT5=Off",
            "-D QUARTER_USE_QT6=On",
        ]
        self._build_standard_cmake(extra_args=extra_args)

    def build_zlib(self, _=None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "zlib.h")):
                print("  Not rebuilding zlib, it is already in the LibPack")
                return
        self._build_standard_cmake()
        # Qt really wants to find these under an alternate name, so just make copies...
        name_mapping = [
            (os.path.join("lib", "z.lib"), os.path.join("lib", "zlib.lib")),
            (os.path.join("bin", "z.dll"), os.path.join("bin", "zlib.dll")),
            (os.path.join("lib", "z.lib"), os.path.join("lib", "zlib1.lib")),
            (os.path.join("bin", "z.dll"), os.path.join("bin", "zlib1.dll")),
        ]
        for name1, name2 in name_mapping:
            full_name1 = os.path.join(self.install_dir, name1)
            full_name2 = os.path.join(self.install_dir, name2)
            if os.path.exists(full_name1) and not os.path.exists(full_name2):
                shutil.copy(full_name1, full_name2)
            elif os.path.exists(full_name2) and not os.path.exists(full_name1):
                shutil.copy(full_name2, full_name1)

    def build_bzip2(self, _=None):
        """The version of BZip2 in widespread use (1.0.8, the most recent official release) do not yet use cMake"""
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "bzlib.h")):
                print("  Not rebuilding bzip2, it is already in the LibPack")
                return
        if sys.platform.startswith("win32"):
            args = [*self.init_script, "&", "nmake", "/f", "makefile.msc"]
            try:
                self._run_streaming(args, "build_log.txt")
                shutil.copyfile("libbz2.lib", os.path.join(self.install_dir, "lib", "libbz2.lib"))
                shutil.copyfile("bzlib.h", os.path.join(self.install_dir, "include", "bzlib.h"))
                shutil.copyfile(
                    "bzlib_private.h", os.path.join(self.install_dir, "include", "bzlib_private.h")
                )
            except subprocess.CalledProcessError as e:
                print("ERROR: Failed to build bzip2 using nmake")
                if e.output:
                    print(e.output.decode("utf-8", errors="replace"))
                exit(1)
        else:
            raise NotImplemented("Non-Windows compilation of bzip2 is not implemented yet")

    def build_pcre2(self, _=None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "pcre2.h")):
                print("  Not rebuilding pcre2, it is already in the LibPack")
                return
        self._build_standard_cmake()

    def build_swig(self, _=None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "bin", "swig") + to_exe()):
                print("  Not rebuilding SWIG, it is already in the LibPack")
                return
        self._build_standard_cmake()

    def build_pivy(self, _=None):
        if self.skip_existing:
            if os.path.exists(
                os.path.join(self.install_dir, "bin", "Lib", "site-packages", "pivy")
            ):
                print("  Not rebuilding pivy, it is already in the LibPack")
                return
        extra_args = []
        self._build_standard_cmake(extra_args)
        if self.mode == BuildMode.DEBUG:
            base = os.path.join(self.install_dir, "bin", "Lib", "site-packages", "pivy")
            os.rename(os.path.join(base, "_coin.pyd"), os.path.join(base, "_coin_d.pyd"))

    def build_libclang(self, _=None):
        """libclang is provided as a platform-specific download by Qt."""
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "clang")):
                print("  Not copying libclang, it is already in the LibPack")
                return
        print("  (not really building libclang, just copying from a build provided by Qt)")
        shutil.copytree("libclang", self.install_dir, dirs_exist_ok=True)

    def build_pyside(self, _=None):
        # Don't use a pip-install for this, we need the linkable libraries and include files for both PySide and
        # Shiboken, which won't get installed by pip, and it needs to be built against the right Python exe
        if self.skip_existing:
            if os.path.exists(
                os.path.join(self.install_dir, "bin", "Lib", "site-packages", "PySide6")
            ):
                print("  Not rebuilding PySide6, it is already in the LibPack")
                return
        python = self.python_exe()
        qtpaths = "--qtpaths=" + os.path.join(self.install_dir, "bin", "qtpaths6") + to_exe()
        parallel = "--parallel=16"
        # Pass environment variables through Python's subprocess env rather than cmd's
        # "set NAME=VALUE & ..." pattern. The cmd form preserves the whitespace before
        # the next "&" separator inside the env value, which historically left a trailing
        # space in CLANG_INSTALL_DIR and broke shiboken's clang resource lookup.
        env = os.environ.copy()
        env["CLANG_INSTALL_DIR"] = self.install_dir
        env["VULKAN_SDK"] = "None"
        if sys.platform.startswith("win32"):
            ssl = "--openssl=" + os.path.join(self.install_dir, "bin", "DLLs")
            python_libs = os.path.join(self.install_dir, "bin", "libs")
            init_call = "call " + subprocess.list2cmdline(self.init_script)
            setup_cmd = subprocess.list2cmdline(
                [python, "setup.py", "install", qtpaths, ssl, parallel]
                + (["--debug"] if self.mode == BuildMode.DEBUG else [])
            )
            wrapper_path = os.path.abspath("build_pyside_wrapper.bat")
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write("@echo off\n")
                f.write(f"{init_call}\n")
                f.write("if errorlevel 1 exit /b %ERRORLEVEL%\n")
                f.write(f"set LIB={python_libs};%LIB%\n")
                f.write(f"{setup_cmd}\n")
            args = [wrapper_path]
        else:
            ssl = "--openssl=" + os.path.join(self.install_dir, "bin", "DLLs")
            args = [python, "setup.py", "install", qtpaths, ssl]
        try:
            self._run_streaming(args, "build_log.txt", env=env)
        except subprocess.CalledProcessError as e:
            print("ERROR: Failed to build Pyside and/or Shiboken")
            if e.output:
                print(e.output.decode("utf-8", errors="replace"))
            exit(1)

    def build_vtk(self, _=None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "share", "licenses", "VTK")):
                print("  Not rebuilding VTK, it is already in the LibPack")
                return
        extra_args = [
            "-D VTK_WRAP_PYTHON=YES",
            "-D VTK_MODULE_ENABLE_VTK_WrappingPythonCore=YES",
            "-D VTK_PYTHON_SITE_PACKAGES_SUFFIX=bin/Lib/site-packages/",
        ]
        if sys.platform.startswith("win32"):
            extra_args.append(
                "-D VTK_MODULE_ENABLE_VTK_IOIOSS=NO",  # Workaround for bug in Visual Studio MSVC 143
            )
            extra_args.append(
                "-D VTK_MODULE_ENABLE_VTK_ioss=NO",  # Workaround for bug in Visual Studio MSVC 143
            )
            if self.mode == BuildMode.DEBUG:
                # Avoid a race condition with the way VS builds PDBs
                extra_args.extend(["-G", "Ninja"])
            else:
                extra_args.append("-D CMAKE_CXX_MP_FLAG=YES")

        print("  (VTK is big, this will take some time)")

        old_strict_mode = self.strict_mode
        self.strict_mode = False
        self._build_standard_cmake(extra_args)
        self.strict_mode = old_strict_mode

    def build_harfbuzz(self, _=None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "harfbuzz")):
                print("  Not rebuilding harfbuzz, it is already in the LibPack")
                return
        # The experimental harfbuzz-gpu library was introduced in HarfBuzz 14.x and fails
        # to link as a shared library on Windows because it references private symbols
        # (_hb_NullPool, _hb_CrapPool) that the main harfbuzz DLL does not export.
        # Nothing in the LibPack consumes harfbuzz-gpu, so it is disabled.
        extra_args = ["-D HB_BUILD_GPU=OFF"]
        self._build_standard_cmake(extra_args)

    def build_libpng(self, _=None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "lib", "libpng")):
                print("  Not rebuilding libpng, it is already in the LibPack")
                return
        self._build_standard_cmake()

    def build_pybind11(self, _=None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "pybind11")):
                print("  Not rebuilding pybind11, it is already in the LibPack")
                return
        self._build_standard_cmake()

    def build_freetype(self, _=None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "freetype2")):
                print("  Not rebuilding freetype, it is already in the LibPack")
                return
        self._build_standard_cmake()
        if self.mode == BuildMode.DEBUG:
            # OCCT *really* wants these libraries named like this:
            shutil.copyfile(
                f"{self.install_dir}/bin/freetyped.dll", f"{self.install_dir}/bin/freetype.dll"
            )
            shutil.copyfile(
                f"{self.install_dir}/lib/freetyped.lib", f"{self.install_dir}/lib/freetype.lib"
            )

    def force_copy(self, src_components: List[str], dst_components: List[str]):
        full_src = self.install_dir
        for src in src_components:
            full_src = os.path.join(full_src, src)
        full_dst = self.install_dir
        for dst in dst_components:
            full_dst = os.path.join(full_dst, dst)
        if not os.path.exists(full_src):
            print(f"    (Can't rename {full_src}, no such file or directory)")
            return
        if os.path.exists(full_dst):
            os.unlink(full_dst)
        shutil.copyfile(full_src, full_dst)

    def build_tcl(self, _=None):
        """tcl does not use cMake"""
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "tcl.h")):
                print("  Not rebuilding tcl, it is already in the LibPack")
                return
        if sys.platform.startswith("win32"):
            try:
                os.chdir("win")
                args = [*self.init_script, "&", "nmake", "/f", "makefile.vc", "release"]
                if self.mode == BuildMode.DEBUG:
                    args.append("OPTS=symbols")
                self._run_streaming(args, "build_log.txt")
                args = [
                    *self.init_script,
                    "&",
                    "nmake",
                    "/f",
                    "makefile.vc",
                    "install",
                    f"INSTALLDIR={self.install_dir}",
                ]
                if self.mode == BuildMode.DEBUG:
                    args.append("OPTS=symbols")
                self._run_streaming(args, "build_log.txt")
                if self.mode == BuildMode.RELEASE:
                    self.force_copy(["bin", "tclsh86t.exe"], ["bin", "tclsh.exe"])
                    self.force_copy(["bin", "tcl86t.dll"], ["bin", "tcl86.dll"])
                    self.force_copy(["lib", "tcl86t.lib"], ["lib", "tcl86.lib"])
                else:
                    self.force_copy(["bin", "tclsh86tg.exe"], ["bin", "tclsh.exe"])
                    self.force_copy(["bin", "tcl86tg.dll"], ["bin", "tcl86.dll"])
                    self.force_copy(["lib", "tcl86tg.lib"], ["lib", "tcl86.lib"])
            except subprocess.CalledProcessError as e:
                print("ERROR: Failed to build tcl using nmake")
                if e.output:
                    print(e.output.decode("utf-8", errors="replace"))
                exit(1)
        else:
            raise NotImplemented("Non-Windows compilation of tcl is not implemented yet")

    def build_tk(self, _=None):
        """tk does not use cMake"""
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "tk.h")):
                print("  Not rebuilding tk, it is already in the LibPack")
                return
        if sys.platform.startswith("win32"):
            try:
                os.chdir("win")
                args = [*self.init_script, "&", "nmake", "/f", "makefile.vc", "release"]
                if self.mode == BuildMode.DEBUG:
                    args.append("OPTS=symbols")
                self._run_streaming(args, "build_log.txt")
                args = [
                    *self.init_script,
                    "&",
                    "nmake",
                    "/f",
                    "makefile.vc ",
                    "install",
                    f"INSTALLDIR={self.install_dir}",
                ]
                if self.mode == BuildMode.DEBUG:
                    args.append("OPTS=symbols")
                self._run_streaming(args, "build_log.txt")
                if self.mode == BuildMode.RELEASE:
                    self.force_copy(["bin", "wish86t.exe"], ["bin", "wish.exe"])
                    self.force_copy(["bin", "tk86t.dll"], ["bin", "tk86.dll"])
                    self.force_copy(["lib", "tk86t.lib"], ["lib", "tk86.lib"])
                else:
                    self.force_copy(["bin", "wish86tg.exe"], ["bin", "wish.exe"])
                    self.force_copy(["bin", "tk86tg.dll"], ["bin", "tk86.dll"])
                    self.force_copy(["lib", "tk86tg.lib"], ["lib", "tk86.lib"])
            except subprocess.CalledProcessError as e:
                print("ERROR: Failed to build tk using nmake")
                if e.output:
                    print(e.output.decode("utf-8", errors="replace"))
                exit(1)
        else:
            raise NotImplemented("Non-Windows compilation of tk is not implemented yet")

    def build_rapidjson(self, _):
        if os.path.exists(os.path.join(self.install_dir, "include", "rapidjson")):
            if self.skip_existing:
                print("  Not re-copying RapidJSON, it is already in the LibPack")
                return
            shutil.rmtree(os.path.join(self.install_dir, "include", "rapidjson"))
        shutil.copytree("include", os.path.join(self.install_dir, "include"), dirs_exist_ok=True)

    def _get_vtk_include_path(self) -> str:
        """
        OpenCASCADE needs a manually-set include path for VTK (the find_package script provided by VTK does not provide
        the include file path, and OpenCASCADE has not been updated to handle this, as of June 2024).
        """
        start_crawl_at = os.path.join(self.install_dir, "include")
        contents = [
            f for f in os.listdir(start_crawl_at) if os.path.isdir(os.path.join(start_crawl_at, f))
        ]
        for item in contents:
            if item.startswith("vtk-"):
                return os.path.join(start_crawl_at, item)
        raise RuntimeError("Could not find VTK include directory for OpenCASCADE")

    def build_opencascade(self, _=None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "cmake", "OpenCASCADEConfig.cmake")):
                print("  Not rebuilding OpenCASCADE, it is already in the LibPack")
                return
        install_dir = self.install_dir
        vtk_include_dir = self._get_vtk_include_path()
        if os.path.sep == "\\":
            # OpenCASCADE's CMake is not tolerant of backslashes in paths, even on Windows
            install_dir = install_dir.replace("\\", "/")
            vtk_include_dir = vtk_include_dir.replace("\\", "/")
        extra_args = [
            f"-D CMAKE_MODULE_PATH={install_dir}/lib/cmake;{install_dir}/share/cmake;{install_dir}",
            f"-D TCL_DIR={install_dir}/include",
            f"-D TK_DIR={install_dir}/include",
            f"-D FREETYPE_DIR={install_dir}/lib/cmake",
            f"-D VTK_DIR={install_dir}/lib/cmake",
            f"-D 3RDPARTY_VTK_INCLUDE_DIRS={vtk_include_dir}",
            f"-D EIGEN_DIR={install_dir}/share/eigen3/cmake",
            f"-D 3RDPARTY_TCL_DLL_DIR={install_dir}/bin",
            f"-D 3RDPARTY_TCL_LIBRARY_DIR={install_dir}/lib",
            f"-D 3RDPARTY_TCL_INCLUDE_DIR={install_dir}/include",
            f"-D 3RDPARTY_TCL_DLL={install_dir}/bin/tcl86.dll",
            f"-D 3RDPARTY_TCL_LIBRARY={install_dir}/lib/tcl86.lib",
            f"-D 3RDPARTY_TK_DLL_DIR={install_dir}/bin",
            f"-D 3RDPARTY_TK_LIBRARY_DIR={install_dir}/lib",
            f"-D 3RDPARTY_TK_INCLUDE_DIR={install_dir}/include",
            f"-D 3RDPARTY_TK_DLL={install_dir}/bin/tk86.dll",
            f"-D 3RDPARTY_TK_LIBRARY={install_dir}/lib/tk86.lib",
            "-D USE_VTK=On",
            "-D USE_FREETYPE=On",
            "-D USE_RAPIDJSON=On",
            "-D USE_EIGEN=On",
            "-D BUILD_CPP_STANDARD=C++17",
            "-D BUILD_RELEASE_DISABLE_EXCEPTIONS=OFF",
            "-D INSTALL_DIR_BIN=bin",
            "-D INSTALL_DIR_LIB=lib",
            "-D CMAKE_POLICY_VERSION_MINIMUM=3.5",
        ]
        if self.mode == BuildMode.DEBUG:
            extra_args.append("-D BUILD_SHARED_LIBRARY_NAME_POSTFIX=d")
        cwd = os.getcwd()
        self._cmake_create_build_dir()
        self._cmake_configure(extra_args)
        self._cmake_build(parallel=False)
        if self.mode == BuildMode.DEBUG and sys.platform.startswith("win32"):
            # On Windows OpenCASCADE is looking in the wrong location for these files (as of 7.7.1) -- just copy them
            # TODO - Don't hardcode the path
            shutil.copytree(
                os.path.join("win64", "vc14", "bind"), os.path.join("win64", "vc14", "bin")
            )
        self._cmake_install()

        os.chdir(cwd)

        if self.mode == BuildMode.DEBUG and sys.platform.startswith("win32"):
            # OCCT's install layout for Debug places DLLs in <install>/bind/ and
            # import libraries in <install>/libd/, ignoring INSTALL_DIR_BIN and
            # INSTALL_DIR_LIB. Downstream consumers (FreeCAD, every workbench
            # .pyd) look for these libraries in <install>/bin/ and <install>/lib/.
            # Merge them in-place and remove the now-empty source directories.
            for src_name, dst_name in (("bind", "bin"), ("libd", "lib")):
                src = os.path.join(self.install_dir, src_name)
                dst = os.path.join(self.install_dir, dst_name)
                if not os.path.isdir(src):
                    continue
                for entry in os.listdir(src):
                    src_path = os.path.join(src, entry)
                    dst_path = os.path.join(dst, entry)
                    if os.path.exists(dst_path):
                        continue
                    shutil.copy2(src_path, dst_path)
                shutil.rmtree(src, onerror=remove_readonly)
            # OCCT's per-config Targets files reference the original bind/ and
            # libd/ paths. Rewrite them to point at the merged locations so that
            # find_package(OpenCASCADE) succeeds in downstream builds.
            occt_targets = glob.glob(
                os.path.join(self.install_dir, "cmake", "OpenCASCADE*Targets-debug.cmake")
            )
            for target_file in occt_targets:
                with open(target_file, "r", encoding="utf-8") as fh:
                    text = fh.read()
                text = text.replace("${_IMPORT_PREFIX}/libd/", "${_IMPORT_PREFIX}/lib/")
                text = text.replace("${_IMPORT_PREFIX}/bind/", "${_IMPORT_PREFIX}/bin/")
                with open(target_file, "w", encoding="utf-8") as fh:
                    fh.write(text)

        # TODO - something is getting messed up in the CMake config output (note the quotes around 26812): for now just
        # drop the line entirely
        # set (OpenCASCADE_CXX_FLAGS    "[...] /wd"26812" /MP /W4")
        with open(
            os.path.join(self.install_dir, "cmake", "OpenCASCADEConfig.cmake"),
            "r",
            encoding="utf-8",
        ) as f:
            occt_cmake_contents = f.readlines()
        with open(
            os.path.join(self.install_dir, "cmake", "OpenCASCADEConfig.cmake"),
            "w",
            encoding="utf-8",
        ) as f:
            for line in occt_cmake_contents:
                if "OpenCASCADE_CXX_FLAGS" not in line:
                    f.write(line + "\n")

    def build_netgen(self, _: None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "share", "netgen")):
                print("  Not rebuilding netgen, it is already in the LibPack")
                return
        extra_args = [
            f"-D CMAKE_FIND_ROOT_PATH={self.install_dir}",
            "-D USE_SUPERBUILD=OFF",
            "-D USE_GUI=OFF",
            "-D USE_INTERNAL_TCL=OFF",
            "-D USE_NATIVE_ARCH=OFF",
            f"-D TCL_DIR={self.install_dir}",
            f"-D TK_DIR={self.install_dir}",
            "-D USE_OCC=On",
            f"-D OpenCASCADE_ROOT={self.install_dir}",
            f"-D USE_PYTHON=OFF",
            f"-D CMAKE_CXX_FLAGS='-D_USE_MATH_DEFINES /EHsc'",
        ]  # To get M_PI on MSVC
        self._build_standard_cmake(extra_args=extra_args)

    def build_hdf5(self, _: None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "hdf5.h")):
                print("  Not rebuilding hdf5, it is already in the LibPack")
                return

        # Per the recommendation of the HDF5 developers, let HDF5 build and link to its own internal
        # copy of ZLib, since their CMake scripts are broken when trying to use a custom compiled
        # version. See e.g. https://github.com/HDFGroup/hdf5/issues/5303
        extra_args = [
            "-D HDF5_BUILD_EXAMPLES=OFF",
            "-D HDF5_BUILD_TOOLS=OFF",
            "-D HDF5_BUILD_UTILS=OFF",
            "-D HDF5_ENABLE_Z_LIB_SUPPORT=ON",
            "-D ZLIB_USE_EXTERNAL=ON",
        ]
        self._build_standard_cmake(extra_args)

    def build_medfile(self, _: None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "medfile.h")):
                print("  Not rebuilding medfile, it is already in the LibPack")
                return
        extra_args = [
            "-D MEDFILE_USE_UNICODE=On",
            "-D MEDFILE_BUILD_TESTS=OFF",
            "-D CMAKE_Fortran_COMPILER=NOTFOUND",
        ]
        old_strict_mode = self.strict_mode
        self.strict_mode = False
        self._build_standard_cmake(extra_args)
        self.strict_mode = old_strict_mode

    def build_gmsh(self, _: None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "bin", "gmsh" + to_exe())):
                print("  Not rebuilding gmsh, it is already in the LibPack")
                return
        extra_args = []
        if sys.platform.startswith("win32"):
            extra_args = [
                "-D ENABLE_OPENMP=No",
                "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
            ]  # Build fails if OpenMP is enabled
        self._build_standard_cmake(extra_args)

    def build_pycxx(self, _: None):
        """PyCXX does not use a cMake-based build system"""
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "bin", "Lib", "site-packages", "CXX")):
                print("  Not rebuilding PyCXX, it is already in the LibPack")
                return
        path_to_python = self.python_exe()
        args = [path_to_python, "setup.py", "install"]
        try:
            self._run_streaming(args, "build_log.txt")
        except subprocess.CalledProcessError as e:
            print("ERROR: Failed to build PyCXX using its custom build script")
            if e.output:
                print(e.output.decode("utf-8", errors="replace"))
            exit(1)

    def build_icu(self, _: None):
        """ICU does not use cMake, but has projects for various OSes"""
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "unicode")):
                print("  Not rebuilding ICU, it is already in the LibPack")
                return

        os.chdir(os.path.join("icu4c", "source"))
        if platform.machine() == "ARM64":
            arch = "ARM64"
        else:
            arch = "x64"
        if sys.platform.startswith("win32"):
            os.chdir("allinone")
            # Find the most recent available WindowsTargetPlatformVersion:
            target = Compiler._get_latest_windows_target_platform_version()
            args = [
                *self.init_script,
                "&",
                "msbuild",
                f"/p:Configuration={str(self.mode).lower()}",
                "/t:Build",
                f"/p:Platform={arch}",
                f"/p:WindowsTargetPlatformVersion={target}",
                "/p:SkipUWP=true",
                "allinone.sln",
            ]
            try:
                self._run_streaming(args, "build_log.txt")
            except subprocess.CalledProcessError as e:
                print("ERROR: Failed to build ICU using its custom build script")
                if e.output:
                    print(e.output.decode("utf-8", errors="replace"))
                exit(1)
            os.chdir(os.path.join("..", ".."))
            bin_dir = os.path.join(self.install_dir, "bin")
            lib_dir = os.path.join(self.install_dir, "lib")
            inc_dir = os.path.join(self.install_dir, "include")
            if sys.platform.startswith("win32"):
                if platform.machine() == "ARM64":
                    shutil.copytree(f"binARM64", bin_dir, dirs_exist_ok=True)
                    shutil.copytree(f"libARM64", lib_dir, dirs_exist_ok=True)
                else:
                    shutil.copytree(f"bin64", bin_dir, dirs_exist_ok=True)
                    shutil.copytree(f"lib64", lib_dir, dirs_exist_ok=True)
            shutil.copytree(f"include", inc_dir, dirs_exist_ok=True)
        else:
            raise NotImplemented("Non-Windows compilation of ICU is not implemented yet")

    @staticmethod
    def _get_latest_windows_target_platform_version() -> Optional[str]:
        base_path = r"C:\Program Files (x86)\Windows Kits\10\Lib"
        if not os.path.exists(base_path):
            return None

        version_dirs = []
        version_pattern = re.compile(r"^\d+\.\d+\.\d+\.\d+$")

        for name in os.listdir(base_path):
            full_path = os.path.join(base_path, name)
            if os.path.isdir(full_path) and version_pattern.match(name):
                version_dirs.append(name)

        if not version_dirs:
            return None

        def version_key(v):
            return [int(x) for x in v.split(".")]

        latest_version = sorted(version_dirs, key=version_key)[-1]
        return latest_version

    def build_xercesc(self, _: None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "xercesc")):
                print("  Not rebuilding xerces-c, it is already in the LibPack")
                return
        extra_args = [
            f"-D ICU_INCLUDE_DIR={self.install_dir}/include",
            f"-D ICU_ROOT={self.install_dir}",
            f"-D ICU_UC_DIR={self.install_dir}",
        ]
        self._build_standard_cmake(extra_args)

    def build_libfmt(self, _: None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "fmt")):
                print("  Not rebuilding libfmt, it is already in the LibPack")
                return
        extra_args = ["-D FMT_TEST=OFF", "-D FMT_DOC=OFF"]
        self._build_standard_cmake(extra_args)

    def build_eigen3(self, _: None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "eigen3")):
                print("  Not rebuilding Eigen3, it is already in the LibPack")
                return
        # These BLAS toolchains require a Fortran compiler, which is often not available. We don't
        # actually NEED these, so just turn them off.
        extra_args = ["-D EIGEN_BUILD_BLAS=OFF", "-D EIGEN_BUILD_LAPACK=OFF"]
        self._build_standard_cmake(extra_args)

    def build_yamlcpp(self, _: None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "yaml-cpp")):
                print("  Not rebuilding yaml-cpp, it is already in the LibPack")
                return
        extra_args = ["-D YAML_BUILD_SHARED_LIBS=ON", "-D CMAKE_POLICY_VERSION_MINIMUM=3.5"]
        self._build_standard_cmake(extra_args)

    def build_opencamlib(self, _: None):
        # opencamlib's CMake installs the Python extension to a relative DESTINATION
        # ("opencamlib") under CMAKE_INSTALL_PREFIX, expecting either scikit-build to
        # supply a wheel root or the build driver to point CMAKE_INSTALL_PREFIX at a
        # site-packages directory (see src/pythonlib/pythonlib.cmake). Other Python
        # C-extensions in this LibPack (for example pivy) detect Python_SITEARCH from
        # CMake's FindPython and install themselves into site-packages directly;
        # opencamlib does not. To ensure it ends up in the right place we override the prefix
        # for this one package so its "opencamlib" destination resolves under the
        # LibPack's site-packages.
        site_packages = os.path.join(self.install_dir, "bin", "Lib", "site-packages")
        if self.skip_existing:
            sentinel_name = "ocl_d.pyd" if self.mode == BuildMode.DEBUG else "ocl.pyd"
            if os.path.exists(os.path.join(site_packages, "opencamlib", sentinel_name)):
                print("  Not rebuilding opencamlib, it is already in the LibPack")
                return
        extra_args = [
            "-D BUILD_CXX_LIB=OFF",
            "-D BUILD_PY_LIB=ON",
            "-D BUILD_DOC=OFF",
            "-D Boost_USE_STATIC_LIBS=OFF",
            f"-D CMAKE_INSTALL_PREFIX={site_packages}",
        ]
        self._build_standard_cmake(extra_args)

    def build_calculix(self, _: None):
        """Cannot currently build Calculix (it's in Fortran, and we only support MSVC toolchain right now). Extract
        the relevant files from the downloaded zipfile and copy them"""
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "bin", "ccx.exe")):
                print("  Not rebuilding Calculix, it is already in the LibPack")
                return
        path_to_ccx_bin = os.path.join(os.getcwd(), "CL35-win64", "bin", "ccx", "218")
        if not os.path.exists(path_to_ccx_bin):
            raise RuntimeError("Could not locate Calculix")
        shutil.copytree(path_to_ccx_bin, os.path.join(self.install_dir, "bin"), dirs_exist_ok=True)
        # The download we use calls the executable ccx218.exe, but FreeCAD would prefer it be called ccx.exe for
        # automatic location of the executable
        shutil.move(
            os.path.join(self.install_dir, "bin", "ccx218.exe"),
            os.path.join(self.install_dir, "bin", "ccx.exe"),
        )

    def build_libE57Format(self, _: None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "E57Format")):
                print("  Not rebuilding libE57Format, it is already in the LibPack")
                return
        extra_args = ["-D E57_BUILD_TEST=OFF"]
        self._build_standard_cmake(extra_args)

    def build_googletest(self, _: None):
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "gtest")):
                print("  Not rebuilding googletest, it is already in the LibPack")
                return
        extra_args = []
        if sys.platform == "win32":
            extra_args.extend(["-D GTEST_FORCE_SHARED_CRT=ON", "-D GTEST_DISABLE_PTHREADS=ON"])
        self._build_standard_cmake(extra_args)

    def build_ifcopenshell(self, _=None):
        """In Release mode: x64 Windows installs from PyPI; ARM64 Windows extracts a
        prebuilt zip from builds.ifcopenshell.org into site-packages.

        In Debug mode: neither PyPI wheel nor S3 prebuilt has cp3XXd ABI artifacts, so
        we source-build from the upstream GitHub repo using LibPack Boost, OpenCASCADE,
        libxml2, and HDF5."""
        if self.mode == BuildMode.DEBUG:
            return self._build_ifcopenshell_debug()
        if platform.machine() != "ARM64" or sys.platform != "win32":
            return
        site_packages = os.path.join(self.install_dir, "bin", "Lib", "site-packages")
        target = os.path.join(site_packages, "ifcopenshell")
        if self.skip_existing and os.path.exists(target):
            print("  Not reinstalling ifcopenshell, it is already in the LibPack")
            return
        source = os.path.join(os.getcwd(), "ifcopenshell")
        if not os.path.exists(source):
            print(f"ERROR: extracted ifcopenshell directory not found at {source}")
            exit(1)
        os.makedirs(site_packages, exist_ok=True)
        if os.path.exists(target):
            shutil.rmtree(target, onerror=remove_readonly)
        shutil.copytree(source, target)

    def _build_ifcopenshell_debug(self):
        """Debug-mode source build of IfcOpenShell. fetch_remote_data clones and
        patches the source for us in Debug because the ifcopenshell entry has
        both a git-repo and a url-ARM64 (the latter is the Release-only prebuilt
        zip)."""
        site_packages = os.path.join(self.install_dir, "bin", "Lib", "site-packages")
        target = os.path.join(site_packages, "ifcopenshell")
        if self.skip_existing and os.path.exists(target):
            print("  Not rebuilding ifcopenshell, it is already in the LibPack")
            return
        cwd = os.getcwd()
        # IfcOpenShell's root CMakeLists.txt lives in the cmake/ subdirectory, not
        # the repo root. Dependencies (OpenCASCADE, HDF5, LibXml2, VTK) are resolved
        # through their installed CMake package configs via CMAKE_PREFIX_PATH rather
        # than passing manual include and library paths, because OCCT installs into a
        # flat layout (inc/ and libd/) that does not match the conventional layout
        # IfcOpenShell's Find modules assume.
        ifc_install_dir = self.install_dir.replace("\\", "/")
        extra_args = [
            "-G",
            "Ninja",
            "-D CMAKE_CXX_STANDARD=17",
            f"-D CMAKE_PREFIX_PATH={ifc_install_dir}",
            f"-D BOOST_ROOT={ifc_install_dir}",
            "-D Boost_USE_STATIC_LIBS=OFF",
            f"-D HDF5_DIR={ifc_install_dir}/cmake",
            f"-D PYTHON_EXECUTABLE={self.python_exe()}",
            "-D BUILD_IFCPYTHON=ON",
            "-D BUILD_IFCMAX=OFF",
            "-D BUILD_GEOMSERVER=OFF",
            "-D BUILD_CONVERT=OFF",
            "-D BUILD_EXAMPLES=OFF",
            "-D BUILD_TESTING=OFF",
            "-D WITH_CGAL=OFF",
            "-D COLLADA_SUPPORT=OFF",
            "-D HDF5_SUPPORT=OFF",
            "-D USE_DEBUG_PYTHON=ON",
            "-D BUILD_ONLY_COMMON_SCHEMAS=ON",
            "-D BUILD_SHARED_LIBS=OFF",
        ]
        # The source layout puts the CMakeLists in cmake/, not the repo root, so we
        # cannot use the standard _build_standard_cmake helper which assumes "..".
        build_dir = os.path.join(cwd, "build-debug")
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir, onerror=remove_readonly)
        os.makedirs(build_dir)
        os.chdir(build_dir)
        # IfcOpenShell's CMakeLists.txt and svgfill/CMakeLists.txt both contain
        # blocks guarded by `if(WIN32 AND NOT DEFINED ENV{CONDA_BUILD})` that force
        # `Boost_USE_STATIC_LIBS=ON` as a non-cache variable, which shadows any -D
        # we pass and causes Boost's modular shared configs to declare themselves
        # version-incompatible. FindHDF5.cmake similarly takes a Windows naming
        # path that does not match our LibPack when CONDA_BUILD is unset. Setting
        # CONDA_BUILD diverts all three sites to the branch that uses the package
        # configs we install, with no other side effects in IfcOpenShell.
        prev_conda_build = os.environ.get("CONDA_BUILD")
        os.environ["CONDA_BUILD"] = "1"
        old_strict_mode = self.strict_mode
        self.strict_mode = False
        try:
            options = self.get_cmake_options()
            options.extend(extra_args)
            options.extend(self._arm64_platform_flag(extra_args))
            options.append("../cmake")
            self._run_cmake(options)
            self._cmake_build()
            self._cmake_install()
        finally:
            self.strict_mode = old_strict_mode
            if prev_conda_build is None:
                os.environ.pop("CONDA_BUILD", None)
            else:
                os.environ["CONDA_BUILD"] = prev_conda_build
            os.chdir(cwd)
