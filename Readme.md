# FreeCAD Windows Library Package (a.k.a. The LibPack)

This repository is to provide libraries needed to compile FreeCAD under Windows. It is targeted at developers who want to test against the newest versions of libraries (potentially even compiling a new version themselves) and isn't designed for day-to-day use. Most developers should use the Pixi build system instead.

LibPack release names include the version of FreeCAD they are expected to work with (e.g. LibPack 26.3.0 v3.5.1 is v3.5.1 of the LibPack, designed to work with FreeCAD 26.3dev). The most recent LibPack is typically only designed to work with the current development branch of FreeCAD.

As of June 2026, the LibPack is designed to work with FreeCAD 26.3dev, and is available for both x64 and ARM64 processors, in Release and Debug modes. Note that Debug builds are *only* compatible with debug builds of FreeCAD and are much larger than the Release builds. Make sure you *really* need it: for most developers, using the Release version and compiling FreeCAD in `RelWithDebInfo` mode is good enough. Builds of the LibPack are available for download from the [Releases page](https://github.com/FreeCAD/FreeCAD-libpack/releases).

The current LibPack, v3.5, is tested to work with [Microsoft Visual Studio](https://en.wikipedia.org/wiki/Microsoft_Visual_C%2B%2B) version 17.14.* (MSVC 143). Visual Studio 2026 may also be used as long as the MSVC v143 toolchain is installed by selecting the v143 toolset via the `--vcvars-ver=14.4` argument to the build script. Note the version numbering is odd: "v143" does in fact correspond to version 14.4. The Debug LibPack is built with `--mode=debug`, produces a `Py_DEBUG` CPython, and source-builds every C extension in the pip set against the `cp3XXd` ABI.

To compile FreeCAD, you will need FreeCAD's source code from the [FreeCAD repository](https://github.com/FreeCAD/FreeCAD). In general, to compile with the LibPack, you will run CMake (either via the GUI or on the command line) and set the following variables:
 * `-D FREECAD_LIBPACK_DIR="C:/Path/To/The/LibPack-1.2.0-v3.5.1-x64-Release"`
 * `-D FREECAD_COPY_LIBPACK_BIN_TO_BUILD=ON`
 * `-D FREECAD_COPY_DEPEND_DIRS_TO_BUILD=ON`
 * `-D FREECAD_COPY_PLUGINS_BIN_TO_BUILD=ON`

(The last three are optional if you intend to run the `INSTALL` target once you have built FreeCAD -- they are needed only if you plan to run directly from the build directory.)

For further details on how to compile FreeCAD using the LibPack, see the [FreeCAD Wiki](https://wiki.freecad.org/Compile_on_Windows).

## Building the LibPack ##

To build the LibPack locally, you will need the following:
 * Network access
 * Visual Studio 17.14.x or later, accessible by CMake. Visual Studio 2026 may be used by selecting the v143 toolset via `--vcvars-ver=14.4`.
 * CMake
 * git
 * 7z (see https://www.7-zip.org)
 * Python >= 3.10 (**not** used inside the LibPack itself, just used to run the creation script)
 * The "requests" Python package (e.g. 'pip install requests')
 * The "diff-match-patch" Python package (e.g. 'pip install diff-match-patch')
 * GNU Bison (for Windows see https://github.com/lexxmark/winflexbison/)
 * (DEBUG BUILD ONLY) Rust toolchain, e.g. https://rustup.rs/
 * (DEBUG BUILD ONLY) Fortran toolchain, e.g. LLVM's flang (https://releases.llvm.org/download.html)

With those pieces in place, the next step is to configure the contents of the LibPack by editing `config.json`. This file lists the source for each LibPack component. Depending on the component, there are three different ways it might be included:
1) Source code checked out from a git repository and built using the local compiler toolchain
2) A pip package installed to the LibPack directory using the LibPack's Python interpreter
   * Note that `pip` itself is installed using the `ensure_pip` Python module
   * On ARM64, PyPI does not publish wheels for every required package. The earlier fallback to unofficial ARM64 wheels has been removed, since PyPI's ARM64 cp314 wheel coverage is now adequate for the rest of the pip set. The remaining gaps are handled as follows:
     * `definitions`, `httptools`, and `sets` have no published ARM64 wheel on PyPI, so pip builds them from the source distribution using the MSVC ARM64 toolchain.
     * `ifcopenshell` has no PyPI wheel for the debug ABI and no ARM64 wheel for any architecture. The config.json entry is hybrid: in Release on x64 it is installed via pip from PyPI, in Release on ARM64 a prebuilt zip is downloaded from builds.ifcopenshell.org and extracted into site-packages by the `build_ifcopenshell` step, and in Debug the source is cloned and built locally with a reduced IFC schema set. All three paths target the same upstream version.
     * `shapely` has no ARM64 wheel on PyPI and no source-build fallback is configured. It is therefore omitted entirely from the ARM64 LibPack, and any FreeCAD feature that depends on it will be unavailable on ARM64 until upstream publishes a wheel or a source-distribution build is integrated.
3) Compressed files downloaded from a remote source and unpacked (e.g. a pre-built binary for Calculix or libclang)

The JSON file just lists out the sources and versions: beyond specifying which method is used for the installation by setting either "git-repo" with "git-ref" (or "git-hash"), or "url" (or "url-x64" and "url-ARM64"), the actual details of how things are built when source code is provided are set in the `compile_all.py` script. An entry may declare both a "git-repo" and a "url-*" to express a hybrid: the source is cloned in Debug builds and the prebuilt artifact is downloaded in Release builds. In `compile_all.py`, the class `Compiler` contains methods following the naming convention `build_XXX` where `XXX` is the "name" provided in the JSON configuration file. If you need to add a compiled or copied package, you must both specify it in the config.json file and provide a matching `build_XXX` method. For pip installation, only the config.json file needs to be edited to include the new dependency.

To change the way a package is compiled, you edit its entry in `compile_all.py`. See the contents of that file for various examples.

## Running the build script ##

```
python.exe create_libpack [arguments]
```
Arguments:
* `-m`, `--mode` -- 'release' or 'debug' (Default: 'release'). Debug builds Py_DEBUG CPython and source-builds the pip set; expect a substantially longer wall-clock time than Release.
* `-c`, `--config` -- Path to a JSON configuration file for this utility (Default: './config.json')
* `-e`, `--no-skip-existing-clone` -- If a given clone (or download) directory exists, delete it and download it again
* `-b`, `--no-skip-existing-build` -- If a given build already exists, run the build process again anyway
* `-s`, `--silent` -- I kow what I'm doing, don't ask me any questions
* `-z`, `--archive` -- After the build completes, compress the finished LibPack directory into a sibling `.7z` archive suitable for distribution.
* `--7zip` -- Path to 7-zip executable if not in PATH
* `--bison` -- Path to Bison executable if not in PATH
* `--vs-version` -- Visual Studio toolchain to build with. Accepts `latest` (default), `2022`, `2026`, or a raw `vswhere` `-version` range such as `[17.0,18.0)`.
* `--vcvars-ver` -- Optional MSVC toolset version to select inside the chosen Visual Studio installation, passed through to `vcvars64.bat` as `-vcvars_ver=VALUE`. Use this to build with the v143 (VS 2022) toolset from a VS 2026 installation, for example `--vcvars-ver=14.4`.
* `--fallback-build-dir` -- Override the fallback build directory used by Qt to avoid Windows path-length limits during its build. Replaces the value declared in `config.json` for the `qt` entry. Supply a short path on a drive that exists on this machine, for example `C:\temp`.

## License

The code for the LibPack creation scripts is licensed under the LGPLv2.1+ license. See the LICENSE file for details. Each individual component in the LibPack is licensed under its own terms: see the individual component directories for details.
