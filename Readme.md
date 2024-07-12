This repository is to provide libraries needed to compile FreeCAD under Windows.

The current LibPack, v3.0, is tested to work with [Microsoft Visual C++](https://en.wikipedia.org/wiki/Microsoft_Visual_C%2B%2B) (a.k.a. MSVC or VC) v14.4 (released mid-2024). It should be possible to use other compilers like MinGW, however this is not tested. This LibPack only supports FreeCAD compilation in Release or RelWithDebInfo mode. It may be possible to compile the LibPack in Debug mode, but changes will certainly be required (and patches are welcome!). In particular, the pip installation of Numpy will have to be adjusted to compile a debug version of Numpy, which will otherwise fail to load from a debug compilation of Python.

For information how to use the LibPack to compile, see this Wiki page: https://wiki.freecadweb.org/Compile_on_Windows

## Building the LibPack ##

To build the LibPack locally, you will need the following:
 * Network access
 * A working compiler toolchain for your system, accessible by cMake
 * CMake
 * git
 * 7z (see https://www.7-zip.org)
 * Python >= 3.8 (**not** used inside the LibPack itself, just used to run the creation script)
 * The "requests" Python package (e.g. 'pip install requests')
 * The "diff-match-patch" Python package (e.g. 'pip install diff-match-patch')
 * Qt - the base installation plus Qt Image Formats and Qt PDF
 * GNU Bison (for Windows see https://github.com/lexxmark/winflexbison/)

With those pieces in place, the next step is to configure the contents of the LibPack by editing `config.json`. This file
lists the source for each LibPack component. Depending on the component, there are three different ways it might be included:
1) Source code checked out from a git repository and built using the local compiler toolchain
1) A pip package installed to the LibPack directory using the LibPack's Python interpreter
   * Note that `pip` itself is installed using the `ensure_pip` Python module
1) Files copied from a local source

The JSON file just lists out the sources and versions: beyond specifying which method is used for the installation by setting
either "git-repo" and "git_ref", "pip-install", or "install-directory", the actual details of how things are built when source
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
* `-m`, `--mode` -- 'release' or 'debug' (Default: 'release')
* `-c`, `--config` -- Path to a JSON configuration file for this utility (Default: './config.json')
* `-w`, `--working` -- Directory to put all the clones and downloads in (Default: './working')
* `-e`, `--no-skip-existing-clone` -- If a given clone (or download) directory exists, delete it and download it again
* `-b`, `--no-skip-existing-build` -- If a given build already exists, run the build process again anyway
* `-s`, `--silent` -- I kow what I'm doing, don't ask me any questions
* `--7zip` -- Path to 7-zip executable
* `--bison` -- Path to Bison executable
