This repository is to provide libraries needed to compile FreeCAD under Windows. It is targetted at developers who want to test against the newest versions of libraries (potentially even compiling a new version themselves), and isn't designed for day-to-day use. Most developers should use the Pixi build system instead.

LibPack release names include the version of FreeCAD they are expected to work with (e.g. LibPack 1.2.0 v3.4.0 is v3.4.0 of the LibPack, designed to work with FreeCAD 1.2dev). The most recent LibPack is typically only designed to work with the current development branch of FreeCAD. To compile release versions of FreeCAD you typically must use older versions of the LibPack. LibPacks release names include the version of FreeCAD they are designed to work with.

The current LibPack, v3.4, is tested to work with [Microsoft Visual Studio](https://en.wikipedia.org/wiki/Microsoft_Visual_C%2B%2B) version 17.14.* (MSVC 143). Visual Studio 2026 may also be used as long as the MSVC v143 toolchain is installed by selecting the v143 toolset via the `--vcvars-ver=14.4` argument to the build script. Note the version numbering is odd: "v143" does in fact correspond to version 14.4. Versions of Visual Studio prior to 17.0 are known not to work (for example, Visual Studio 2019 will not work with LibPack v3, and must use LibPack v2.11). The LibPack has been tested on x64 and ARM64 processors. If using a pre-built package, please make sure you download the one for your architecture. It should be possible to use other compilers such as MinGW, however this is not tested. This LibPack only supports FreeCAD compilation in Release or RelWithDebInfo mode. It may be possible to compile the LibPack in Debug mode, but changes will certainly be required (and patches are welcome). In particular, the pip installation of Numpy will have to be adjusted to compile a debug version of Numpy, which will otherwise fail to load from a debug compilation of Python.

To compile FreeCAD you will need FreeCAD's source code from the [FreeCAD repository](https://github.com/FreeCAD/FreeCAD).
Version 3.4 of the LibPack was created in May 2026 and is intended for the FreeCAD 1.2 development branch. In
general, to compile with the LibPack, you will run CMake (either via the GUI or on the command line) and set the following
variables:
 * `-D FREECAD_LIBPACK_DIR="C:/Path/To/The/LibPack-1.2.0-v3.4.0-Release"`
 * `-D FREECAD_COPY_LIBPACK_BIN_TO_BUILD=ON`
 * `-D FREECAD_COPY_DEPEND_DIRS_TO_BUILD=ON`
 * `-D FREECAD_COPY_PLUGINS_BIN_TO_BUILD=ON`

(The last three are optional if you intend to run the `INSTALL` target once you have built FreeCAD -- they are needed only
if you plan to run directly from the build directory.)

For further details on how to compile FreeCAD using the LibPack, see the [FreeCAD Wiki](https://wiki.freecad.org/Compile_on_Windows).

## Building the LibPack ##

To build the LibPack locally, you will need the following:
 * Network access
 * Visual Studio 17.14.x or later, accessible by CMake. Visual Studio 2026 may be used by selecting the v143 toolset via `--vcvars-ver=14.4`.
 * CMake
 * git
 * 7z (see https://www.7-zip.org)
 * Python >= 3.8 (**not** used inside the LibPack itself, just used to run the creation script)
 * The "requests" Python package (e.g. 'pip install requests')
 * The "diff-match-patch" Python package (e.g. 'pip install diff-match-patch')
 * GNU Bison (for Windows see https://github.com/lexxmark/winflexbison/)

With those pieces in place, the next step is to configure the contents of the LibPack by editing `config.json`. This file
lists the source for each LibPack component. Depending on the component, there are three different ways it might be included:
1) Source code checked out from a git repository and built using the local compiler toolchain
2) A pip package installed to the LibPack directory using the LibPack's Python interpreter
   * Note that `pip` itself is installed using the `ensure_pip` Python module
   * On ARM64, PyPI does not publish wheels for every required package. The earlier fallback to unofficial ARM64 wheels has been removed, since PyPI's ARM64 cp314 wheel coverage is now adequate for the rest of the pip set. The remaining gaps are handled as follows:
     * `definitions`, `httptools`, and `sets` have no published ARM64 wheel on PyPI, so pip builds them from the source distribution using the MSVC ARM64 toolchain.
     * `ifcopenshell` has no PyPI wheel for any architecture. On x64 it is installed from PyPI; on ARM64 a prebuilt zip is downloaded from builds.ifcopenshell.org and extracted into site-packages by the `build_ifcopenshell` step. Both architectures are pinned to the same upstream version.
     * `shapely` has no ARM64 wheel on PyPI and no source-build fallback is configured. It is therefore omitted entirely from the ARM64 LibPack, and any FreeCAD feature that depends on it will be unavailable on ARM64 until upstream publishes a wheel or a source-distribution build is integrated.
3) Compressed files downloaded from a remote source and unpacked (e.g. a pre-built binary for Calculix or libclang)

The JSON file just lists out the sources and versions: beyond specifying which method is used for the installation by setting
either "git-repo" and "git_ref" (or "git-hash"), or "url" (or "url-x64" and "url-ARM64"), the actual details of how things are built when source
code is provided are set in the `compile_all.py` script. In that file, the class `Compiler` contains methods following the
naming convention `build_XXX` where `XXX` is the "name" provided in the JSON configuration file. If you need to add a compiled
or copied package, you must both specify it in the config.json file and provide a matching `build_XXX` method. For pip
installation, only the config.json file needs to be edited to include the new dependency.

To change the way a package is compiled, you edit its entry in `compile_all.py`. See the contents of that file for various
examples.

## Running the build script ##

```
python.exe create_libpack [arguments]
```
Arguments:
* `-m`, `--mode` -- 'release' or 'debug' (Default: 'release' -- debug is not currently functional)
* `-c`, `--config` -- Path to a JSON configuration file for this utility (Default: './config.json')
* `-w`, `--working` -- Directory to put all the clones and downloads in (Default: './working')
* `-e`, `--no-skip-existing-clone` -- If a given clone (or download) directory exists, delete it and download it again
* `-b`, `--no-skip-existing-build` -- If a given build already exists, run the build process again anyway
* `-s`, `--silent` -- I kow what I'm doing, don't ask me any questions
* `--7zip` -- Path to 7-zip executable if not in PATH
* `--bison` -- Path to Bison executable if not in PATH
* `--vs-version` -- Visual Studio toolchain to build with. Accepts `latest` (default), `2022`, `2026`, or a raw `vswhere` `-version` range such as `[17.0,18.0)`.
* `--vcvars-ver` -- Optional MSVC toolset version to select inside the chosen Visual Studio installation, passed through to `vcvars64.bat` as `-vcvars_ver=VALUE`. Use this to build with the v143 (VS 2022) toolset from a VS 2026 installation, for example `--vcvars-ver=14.4`.
* `--fallback-build-dir` -- Override the fallback build directory used by Qt to avoid Windows path-length limits during its build. Replaces the value declared in `config.json` for the `qt` entry. Supply a short path on a drive that exists on this machine, for example `C:\temp`.

## License

The code for the LibPack creation scripts is licensed under the LGPLv2.1+ license. See the LICENSE file for details. Each
individual component in the LibPack is licensed under its own terms: see the individual component directories for details.
