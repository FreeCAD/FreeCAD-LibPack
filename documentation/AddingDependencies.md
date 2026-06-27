# Adding a New Dependency to the LibPack

This document explains how to add a new component to the FreeCAD LibPack. It is written for contributors who edit this repository, not for people who consume a finished LibPack. It assumes you are comfortable with reading code and compiling C and C++ projects, but it does not assume any prior familiarity with the LibPack scripts themselves.

## TL;DR

If you already know your way around the build, here is the short version.

1. Decide how the dependency is delivered: a pure `pip`-installed Python package, source code obtained using git and then built, a prebuilt download, or a hybrid that does both depending on build mode.
2. For a pure `pip` package, add a pinned entry to the `requirements` list inside the `python` entry of `config.json`. You are done. There is nothing else to do.
3. For anything else, add an entry to the `content` array of `config.json` (with `git-repo` plus `git-ref` or `git-hash`, or with a `url` / `url-x64` / `url-ARM64`), and add a matching `build_<name>` method to the `Compiler` class in `compile_all.py`. The method name **must** be exactly `build_` followed by the `name` field from the JSON.
4. Order matters. Place the entry in `config.json` after everything it depends on, because builds run top to bottom and install into one shared directory.
5. In the `build_<name>` method, honor `self.skip_existing` by checking for a sentinel artifact and returning early if it exists, and prefix every MSVC subprocess call with `self.init_script` and `"&"`. For a normal CMake project, the body can be as little as `self._build_standard_cmake()`.
6. If you need to modify upstream source, generate a patch with `generate_patch.py` (it uses the `diff_match_patch` format, not unified diff) and list it under `patches` in the entry.
7. Consider ARM64 and Debug mode. Some dependencies behave differently, or are skipped entirely, on one architecture or in one build mode.
8. Validate: run the unit tests, run a real build, confirm the component appears in the output and the manifest, and run `pre-commit run --all-files`.

The rest of this document explains each of these steps in detail.

## Background: what the LibPack is

The LibPack is a bundle of pre-compiled dependencies (Python, Qt, OpenCASCADE, VTK, Coin, Boost, and many others) used to build FreeCAD on Windows. This repository does not contain FreeCAD itself; it contains the Python scripts that clone, download, patch, build, and clean up those dependencies and assemble them into a single redistributable directory. The target platform is Windows on x64 and ARM64, built with the MSVC v143 toolchain, in both Release and Debug configurations.

Three files do most of the work, and adding a dependency almost always means editing the first one, often the second, and occasionally the third.

- `config.json` is a declarative manifest. It lists every component, its version, and how to fetch it. This is the only file you *must* edit for every new dependency.
- `compile_all.py` contains the `Compiler` class. For each component that is built or copied (as opposed to pip-installed), there is a method named `build_<name>` that knows how to turn the fetched source or archive into installed files. You edit this file whenever the dependency is not a pure pip package.
- `path_cleaner.py` runs after the build and makes the LibPack relocatable by rewriting absolute build-time paths. You edit this file only in the relatively rare case that your dependency leaves machine-specific paths behind.

## Step 1: Choose an installation method

Before writing anything, decide how the dependency will be delivered. This choice determines everything that follows.

- A pure pip package is published to PyPI with wheels for the platforms you need. This is the lowest effort option and does not require a `build_<name>` method.
- A source build from git is cloned with git and compiled locally with the MSVC toolchain. This requires a `build_<name>` method.
- A prebuilt download is fetched as a compressed archive from a URL and unpacked. This still requires a `build_<name>` method, although that method is usually only an extract-and-copy step.
- A hybrid declares both a `git-repo` and a `url-*`. The source is cloned and built in Debug mode, while the prebuilt artifact is downloaded in Release mode. The `ifcopenshell` entry is the worked example of this.

Prefer pip when wheels exist for all required platforms, because it is by far the least work to maintain. Prefer a source build for everything else, when possible. Fall back to prebuilt download when upstream publishes reliable binaries but the source is impractical to build here (the Fortran-based `calculix` is an example).

## Step 2: The pip-only path

If the dependency is a pure pip package, the entire change is to add one line to the `requirements` array inside the `python` entry of `config.json`. Pin the exact version with `==`, and keep the list alphabetized to match the surrounding entries.

```python
"shapely==2.1.2; platform_machine != \"ARM64\"",
```

The trailing portion after the semicolon is a standard pip environment marker. The example above installs the package on every platform except ARM64, which is useful when no ARM64 wheel exists. There is no `build_<name>` method to write. After Python itself is built, the script installs the entire requirements list automatically.

A few platform notes are worth considering. On ARM64, PyPI does not publish wheels for every package. Where a wheel is missing but a source distribution can be compiled with the MSVC ARM64 toolchain, pip builds it automatically from the sdist; `definitions`, `httptools`, and `sets` are handled this way. Where neither a wheel nor a workable source build exists, the package is omitted from the ARM64 LibPack entirely, which is the current situation for `shapely`. Any FreeCAD feature depending on an omitted package is unavailable on that architecture until upstream publishes a wheel.

## Step 3: The source-build or download path

Anything that is not a pure pip package requires two coordinated edits: an entry in `config.json` and a matching method in `compile_all.py`.

### Step 3a: Add the config.json entry

Add an object to the `content` array. Every entry has a `name`, which is both the human-readable identifier and the basis for the build method name, plus a fetch directive.

For a git source, supply `git-repo` together with either `git-ref` (a tag or branch, preferred) or `git-hash` (a specific commit, used when there is no suitable tag). When you pin to a hash, record the date of that commit in a `note` field so that future maintainers understand what they are looking at.

```json
{
    "name": "rapidjson",
    "git-repo": "https://github.com/Tencent/rapidjson",
    "git-hash": "24b5e7a8b27f42fa16b96fc70aade9106cf7102f",
    "note": "Git hash from 17 July 2025"
}
```

For a prebuilt download, supply `url` for an architecture-independent archive, or `url-x64` and `url-ARM64` for architecture-specific archives.

```json
{
    "name": "libclang",
    "url-x64": "https://download.qt.io/.../libclang-...-windows-vs2022_64.7z",
    "url-ARM64": "https://download.qt.io/.../libclang-...-windows-vs2022_arm64.7z"
}
```

Optional fields you may use:

- `patches` is a list of patch file paths, relative to the repository root, applied only when the source is cloned. See Step 5.
- `note` is free text. Use it to record version pins, the rationale for a hash, or a reminder of when a patch can be removed. It's not even really a field, it's ignored by the actual LibPack construction and is only used to provide context in cases where it's necessary. Unknown JSON fields are not an error, so you can add whatever you like.
- `fallback-build-dir` provides a short build path to work around Windows path-length limits. Only Qt currently needs this.

Placement within the `content` array is significant. The build iterates the array in order and installs every component into one shared directory (`self.install_dir`). A dependency must therefore appear before any component that consumes it. If your new library is needed by, for example, OpenCASCADE, it must be listed *above* the `opencascade` entry.

### Step 3b: Add the `build_<name>` method

The `Compiler.compile_all` method walks the `content` array and, for each entry, looks for a method named `build_` followed by the entry's `name`. If that method does not exist, the build prints a message and aborts. There is no implicit default. The exception is pip packages, which are covered by the `python` entry's own machinery and need no per-package method.

The method receives the entry's dictionary as its single argument, in case it needs values from the JSON. A minimal method for a well-behaved CMake project is a single call.

```python
def build_mylibrary(self, _=None):
    if self.skip_existing:
        if os.path.exists(os.path.join(self.install_dir, "include", "mylibrary.h")):
            print("  Not rebuilding mylibrary, it is already in the LibPack")
            return
    self._build_standard_cmake()
```

`self._build_standard_cmake()` runs the configure, build, and install sequence and automatically passes the long list of `-D` options returned by `get_cmake_options()`, which point every dependency at the shared install directory. You should pass only the options specific to your package, through the `extra_args` parameter, and never re-specify the shared options.

```python
extra_args = [
    "-D MYLIBRARY_BUILD_TESTS=Off",
    "-D MYLIBRARY_BUILD_EXAMPLES=Off",
]
self._build_standard_cmake(extra_args=extra_args)
```

Not every project uses CMake. For projects with a hand-written makefile, invoke the tool directly; `build_bzip2` is the reference for the nmake pattern. For prebuilt downloads, the method usually only extracts the archive and copies the contents into the install directory; `build_libclang` and `build_calculix` are the references there.

## Step 4: Conventions every build method must follow

These conventions are applied consistently throughout `compile_all.py`. Match them.

Honor `self.skip_existing`. At the start of the method, check for a sentinel artifact that exists only after a successful build (a header, a library, a license file, or a `site-packages` directory) and return early if it is present. This is what makes incremental rebuilds fast, because unchanged components are not rebuilt on every run.

Prefix MSVC-dependent subprocess calls with `self.init_script` followed by `"&"`. For example, `[self.init_script, "&", "nmake", "/f", "makefile.msc"]`. The init script sources the correct vcvars batch file (x64 or ARM64) so the compiler environment is in place before your command runs. CMake invocations made through the standard helpers already do this; you only need to remember it for direct tool calls.

Stream and log output through the established helpers. Use `self._run_streaming(args, "build_log.txt")` rather than calling `subprocess` directly, catch `subprocess.CalledProcessError`, print a clear error, and exit non-zero on failure. This keeps build logs consistent and makes failures legible.

Handle version-stamped install directories. A few libraries install into a directory whose name includes their version, which later CMake invocations cannot guess. After building such a library, probe the install tree and store the discovered path on the `Compiler` instance. Boost (stored on `self.boost_include_path`) and Coin (stored on `self.coin_cmake_path`) are the two existing cases; study `build_coin` and its `_configure_coin_cmake_path` helper before adding a third. If you bump the Boost or Coin version, re-verify that these probes still find the new directory.

## Step 5: Patches

Sometimes upstream source does not compile cleanly here, whether because of an MSVC quirk, a C++ standard mismatch, or a bug that is fixed upstream but not yet released. The LibPack applies small patches in those cases.

Patches in this repository use the `diff_match_patch` format (the Google library), not unified diff. Each patch file begins with a `@@@ filename @@@` header, which allows one patch file to target several files. Do not paste a unified diff into the `patches` directory; it will not apply.

Generate a patch by making your correction to a copy of the file, then running the generator:

```shell
python generate_patch.py original_file corrected_file patches/mylibrary-01-description.patch
```

List the resulting file under the entry's `patches` array in `config.json`. Patches are applied only on the clone path, that is, only when the source is built from git, not when a prebuilt archive is downloaded. When a patch exists only to work around an issue that upstream has already fixed, add a `note` recording when it can be removed.

## Step 6: Platform and mode considerations

The LibPack is built for two architectures and two modes, and a new dependency must work, or be deliberately skipped, in each combination.

ARM64 has several special cases. The init script targets the ARM64 vcvars, CMake invocations add `-A ARM64`, and a few libraries need architecture-specific options. When you write platform-conditional code, test `platform.machine() == "ARM64"` and `sys.platform == "win32"` the same way the existing code does. If a prebuilt download has no ARM64 archive, decide whether to build from source on that architecture or to skip the component there.

Debug mode builds a `Py_DEBUG` CPython and source-builds every C extension in the pip set against the debug ABI. As a result several entries exist only to support Debug builds, because in Release the same functionality arrives bundled inside a PyPI wheel. `libiconv`, `libxml2`, `libxslt`, `libjpeg`, and `openblas` are all Debug-only in exactly this sense. The pattern in code is an early return: the `build_<name>` method checks the mode and does nothing in the configuration where the dependency is not needed (see `build_libjpeg` and `build_openblas`).

Some versions are pinned because something downstream constrains them, and these must not be bumped casually. HDF5 must stay on the 1.14.x series for the Salome medfile requirement, libclang is pinned at 21.1.2 to avoid shiboken parser bugs, and the Debug libxml2 tracks whatever version lxml bundles so the ABI matches. If your new dependency introduces a similar constraint, document it in a `note` on the entry.

## Step 7: Post-build cleanup

After every component is built, `path_cleaner.py` runs to make the result relocatable. It rewrites absolute build-time paths in `*.cmake` files to `${CMAKE_CURRENT_SOURCE_DIR}`, strips components that are not shipped, and removes executables that are not needed at runtime. There is also a special case (`correct_opencascade_freetype_ref`) for a path that OpenCASCADE hardcodes in a way the generic rewrite cannot reach.

Most dependencies need nothing here. Check whether yours leaves absolute paths in its installed CMake files, ships large components FreeCAD does not use, or installs executables that have no runtime purpose. If so, add a corresponding rule to `path_cleaner.py`.

## Step 8: Validate

Confirm the change before considering it finished.

- Run the unit tests: `python -m unittest discover -p "test_*.py"`. If you changed behavior that the tests cover, update them; the relevant files are `test_compile_all.py`, `test_create_libpack.py`, `test_generate_patch.py`, and `test_path_cleaner.py`.
- Run a real build: `python create_libpack.py` with the arguments documented in `Readme.md`. To exercise only your new entry without rebuilding everything, use the force-rebuild and no-skip-existing options.
- Confirm the new component appears under `working/LibPack-.../` and is listed in the generated manifest.
- Build the configurations your dependency claims to support. At minimum confirm Release on x64; confirm Debug and ARM64 as well when the component is meant to support them.
- Run formatting and linting: `pre-commit run --all-files`. Black is configured for a line length of 100.

## Checklist

- [ ] Chose the appropriate installation method (pip, source, prebuilt, or hybrid).
- [ ] Added the `config.json` entry, correctly ordered, with the version pinned and a `note` where useful.
- [ ] Added the matching `build_<name>` method (for everything that is not pure pip), honoring `self.skip_existing` and prefixing MSVC calls with `self.init_script`.
- [ ] Generated any patches in the `diff_match_patch` format and listed them under `patches`.
- [ ] Considered both architectures (x64 and ARM64) and both modes (Release and Debug).
- [ ] Updated `path_cleaner.py` if the component leaves machine-specific paths or unwanted files.
- [ ] Confirmed the tests pass, a real build succeeds, and the component is present in the output and manifest.
- [ ] Confirmed `pre-commit run --all-files` is clean.

## Worked examples to study

The existing build methods are the best reference. Good starting points, by pattern:

- Standard CMake with no special handling: `build_quarter`, `build_pcre2`.
- CMake followed by file fixups: `build_zlib`, which copies libraries to the alternate names Qt expects.
- A non-CMake build driven by nmake: `build_bzip2`.
- Discovery of a version-stamped install path: `build_coin` and `build_boost`.
- A prebuilt archive that is downloaded and extracted: `build_libclang`, `build_calculix`.
- A hybrid that builds from source in Debug and downloads a prebuilt artifact in Release: `build_ifcopenshell`.
- A pure pip package: any line in the `requirements` list of the `python` entry.
