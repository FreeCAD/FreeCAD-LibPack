#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

# Prerequisites:
#   * Network access
#   * A working compiler toolchain for your system, accessible by cMake
#   * CMake
#   * git
#   * 7z (see https://www.7-zip.org)
#   * Some version of Python that can run this file
#   * The "requests" Python package (e.g. 'pip install requests')
#   * The "diff-match-patch" Python package (e.g. 'pip install diff-match-patch')
#   * Qt - the base installation plus Qt Image Formats, Qt Webengine, Qt Webview, and Qt PDF
#   * GNU Bison (for Windows see https://github.com/lexxmark/winflexbison/)

# Note about Python: Python includes the following dependencies when built on Windows (as of v3.11.5)
#   bzip2
#   sqlite
#   xz
#   zlib
#   libffi
#   openssl-bin
#   tcltk
# At present these are not re-used to create the rest of the LibPack -- if needed, they are rebuilt from source

import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tarfile
from urllib.parse import urlparse
import path_cleaner

try:
    import requests
except ImportError:
    print("Please pip install requests")
    exit(1)

try:
    import diff_match_patch
except ImportError:
    print("Please pip install diff-match-patch")
    exit(1)

import compile_all

path_to_7zip = r"C:\Program Files\7-Zip\7z.exe"
path_to_bison = r"C:\Program Files\win-flex-bison\win_bison.exe"
vswhere = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"


def remove_readonly(func, path, _) -> None:
    """Remove a read-only file."""

    os.chmod(path, stat.S_IWRITE)
    func(path)


def delete_existing(path: str, silent: bool = False):
    """Delete a directory tree, with optional confirmation sequence"""
    if os.path.exists(path):
        if not silent:
            response = input(f"Really delete entire path {path}? y/N ")
            if response.lower() != "y":
                print(f"NOT removing {path}")
                return
            print(f"Removing {path} prior to beginning")
        shutil.rmtree(path, onerror=remove_readonly)


def load_config(path: str) -> dict:
    """Load a JSON-formatted configuration file for this utility"""
    if not os.path.exists(path):
        print(f"ERROR: No such config file '{path}'")
        exit(1)
    with open(path, "r", encoding="utf-8") as f:
        config_data = f.read()
        try:
            return json.loads(config_data)
        except json.JSONDecodeError:
            print("ERROR: The config file does not contain valid JSON data")
            exit(1)


def create_libpack_dir(config: dict, mode: compile_all.BuildMode) -> str:
    """Create a new directory for this LibPack compilation, using the version of FreeCAD, the version of
    the LibPack, and whether it's in release or debug mode. Returns the name of the created directory.
    """

    dirname = compile_all.libpack_dir(config, mode)
    if os.path.exists(dirname):
        backup_name = dirname + "-backup-" + "a"
        while os.path.exists(backup_name):
            if backup_name[-1] == "z":
                print(
                    "You have too many old LibPack backup directories. Please delete some of them."
                )
                exit(1)
            backup_name = backup_name[:-1] + chr(ord(backup_name[-1]) + 1)

        os.rename(dirname, backup_name)
    if not os.path.exists(dirname):
        os.mkdir(dirname)
    dirname = os.path.join(dirname, "bin")
    if not os.path.exists(dirname):
        os.mkdir(dirname)
    return dirname


def _select_url(item: dict) -> str | None:
    if "url" in item:
        return item["url"]
    if platform.machine() == "ARM64" and "url-ARM64" in item:
        return item["url-ARM64"]
    if "url-x64" in item:
        return item["url-x64"]
    return None


def fetch_remote_data(config: dict, mode: compile_all.BuildMode, skip_existing: bool = False):
    """Clone the required repos and download the URLs.

    Entries that provide both a `git-repo` and a `url-*` are treated as hybrid:
    Debug builds clone the source (so the package can be built locally against
    the Py_DEBUG ABI), Release builds prefer the prebuilt URL, and if no URL
    matches the current architecture nothing is fetched (the build step is
    expected to handle the package some other way, e.g. via pip)."""
    content = config["content"]
    is_debug = mode == compile_all.BuildMode.DEBUG
    for item in content:
        if skip_existing and os.path.exists(item["name"]):
            continue
        has_git = "git-repo" in item
        has_any_url = any(k in item for k in ("url", "url-ARM64", "url-x64"))
        url = _select_url(item)
        if ("git-ref" in item or "git-hash" in item) and not has_git:
            print(f"ERROR: found a git ref/hash without a git repo for {item['name']}")
            exit()
        if has_git and (not has_any_url or is_debug):
            clone(
                item["name"],
                item["git-repo"],
                item.get("git-ref"),
                item.get("git-hash"),
            )
            if "patches" in item:
                cwd = os.getcwd()
                os.chdir(item["name"])
                compile_all.patch_files(item["patches"])
                os.chdir(cwd)
        elif url is not None:
            download(item["name"], url)
        else:
            os.makedirs(item["name"], exist_ok=True)


def clone(name: str, url: str, ref: str = None, hash: str = None):
    """Shallow clones a git repo at the given ref using a system-installed git"""
    try:
        if ref is None:
            print(f"Cloning {url}")
        else:
            print(f"Cloning {url} at {ref}")
        args = ["git", "clone"]
        if ref is not None:
            args.extend(["--branch", ref, "--depth", "1"])
        elif hash is None:
            args.extend(["--depth", "1"])
        args.extend([url, name])
        subprocess.run(args, capture_output=True, check=True)

        if hash is not None:
            print(f"  Checking out {hash}")
            os.chdir(name)
            subprocess.run(["git", "checkout", hash], capture_output=True, check=True)
            os.chdir("..")

        # Qt's qt5 supermodule contains dozens of submodules and we only build a few. Its
        # configure.bat handles selective submodule initialization via -init-submodules, so
        # cloning the supermodule alone is much faster than recursively initializing every
        # submodule here.
        if name != "qt":
            os.chdir(name)
            subprocess.run(
                ["git", "submodule", "update", "--init", "--recursive", "--depth", "1"],
                capture_output=True,
                check=True,
            )
            os.chdir("..")

    except subprocess.CalledProcessError as e:
        print(f"ERROR: failed to clone git repo {url} at ref {ref}")
        print(e.output)
        exit(e.returncode)


def download(name: str, url: str):
    """Directly downloads some sort of compressed format file and decompresses it (either using an internal
    python method, or using a system-installed 7-zip)"""
    print(f"Downloading {name} from {url}")
    os.mkdir(name)
    request_result = requests.get(url)
    parsed_url = urlparse(url)
    filename = parsed_url.path.rsplit("/", 1)[-1]
    with open(os.path.join(name, filename), "wb") as f:
        f.write(request_result.content)
    decompress(name, filename)


def decompress(name: str, filename: str):
    original_dir = os.getcwd()
    os.chdir(name)
    if filename.endswith("7z") or filename.endswith("7zip"):
        try:
            subprocess.run([path_to_7zip, "x", filename], capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"ERROR: failed to unzip {filename} at from {name} using {path_to_7zip}")
            print(e.output)
            exit(e.returncode)
    elif (
        filename.endswith(".tar.gz")
        or filename.endswith(".tar.bz2")
        or filename.endswith(".tar.xz")
    ):
        try:
            with tarfile.open(filename) as f:
                f.extractall(filter="data")
        except tarfile.TarError as e:
            print(e)
            exit(1)
    else:  # Try to use 7-zip to see if it's something understandable to that program
        try:
            subprocess.run([path_to_7zip, "x", filename], capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            print("ERROR: failed to unzip {filename} at from {name} using {path_to_7zip}")
            print(e.output)
            exit(e.returncode)
    os.chdir(original_dir)


def create_archive(libpack_path: str) -> str:
    """Pack the finished LibPack directory into a sibling .7z archive using the
    system 7-zip executable. The archive is written next to the directory and
    overwrites any existing archive of the same name."""
    parent = os.path.dirname(libpack_path)
    name = os.path.basename(libpack_path)
    archive_name = name + ".7z"
    archive_path = os.path.join(parent, archive_name)
    if os.path.exists(archive_path):
        os.remove(archive_path)
    print(f"Creating 7-zip archive {archive_path}")
    cwd = os.getcwd()
    os.chdir(parent)
    try:
        try:
            subprocess.run([path_to_7zip, "a", "-t7z", archive_name, name], check=True)
        except subprocess.CalledProcessError as e:
            print(f"ERROR: failed to create 7-zip archive {archive_path} using {path_to_7zip}")
            exit(e.returncode)
    finally:
        os.chdir(cwd)
    return archive_path


def write_manifest(outer_config: dict, mode_used: compile_all.BuildMode):
    manifest_file = os.path.join(compile_all.libpack_dir(outer_config, mode_used), "manifest.json")
    with open(manifest_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(outer_config["content"], indent="    "))
    version_file = os.path.join(
        compile_all.libpack_dir(outer_config, mode_used), "FREECAD_LIBPACK_VERSION"
    )
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(outer_config["LibPack-version"])


VS_VERSION_RANGES = {
    "2022": "[17.0,18.0)",
    "2026": "[18.0,19.0)",
}


def list_msvc_tools_versions(vs_install_path: str) -> list:
    """Return the names of MSVC tools directories that contain a working compiler. Used
    in error messages to show the user what is actually installed."""
    msvc_root = Path(vs_install_path) / "VC" / "Tools" / "MSVC"
    if not msvc_root.is_dir():
        return []
    versions = []
    for d in sorted(msvc_root.iterdir()):
        if d.is_dir() and _msvc_dir_has_cl(d):
            versions.append(d.name)
    return versions


def _msvc_dir_has_cl(tools_dir: Path) -> bool:
    """A populated MSVC tools directory contains cl.exe under at least one Host*/<arch>
    bin directory. Empty stub directories (sometimes created by the VS installer when
    only headers or redistributables are selected) are filtered out."""
    bin_root = tools_dir / "bin"
    if not bin_root.is_dir():
        return False
    for host in bin_root.iterdir():
        if not host.is_dir() or not host.name.lower().startswith("host"):
            continue
        for target in host.iterdir():
            if (target / "cl.exe").exists():
                return True
    return False


def resolve_msvc_tools_version(vs_install_path: str, requested: str) -> str:
    """Resolve a possibly-partial MSVC tools version (for example '14.4' or '14.44') to
    the full installed version (for example '14.44.35207') by scanning the
    VC\\Tools\\MSVC directories. Empty stub directories are skipped. Returns the
    highest matching version, or None if no installed compiler matches the prefix."""
    msvc_root = Path(vs_install_path) / "VC" / "Tools" / "MSVC"
    if not msvc_root.is_dir():
        return None
    matches = []
    for d in msvc_root.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        if not name.startswith(requested):
            continue
        next_char = name[len(requested) : len(requested) + 1]
        if next_char and next_char != "." and not next_char.isdigit():
            continue
        if _msvc_dir_has_cl(d):
            matches.append(name)
    if not matches:
        return None
    matches.sort(key=lambda v: tuple(int(p) for p in v.split(".") if p.isdigit()), reverse=True)
    return matches[0]


def build_vswhere_args(vs_version: str) -> list:
    """Build the vswhere command line for the requested Visual Studio selection. The
    'latest' value selects whatever vswhere considers newest. Nicer aliases like
    '2022' and '2026' translate to vswhere's -version range syntax. Any other value is
    sent to -version directly, allowing callers to pass a raw range such as
    '[17.0,18.0)' if needed."""
    base = [
        vswhere,
        "-products",
        "*",
        "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",  # Dirty lie, works on ARM too
        "-property",
        "installationPath",
    ]
    if vs_version == "latest":
        return [vswhere, "-latest"] + base[1:]
    version_range = VS_VERSION_RANGES.get(vs_version, vs_version)
    return [base[0], "-version", version_range] + base[1:]


@contextmanager
def prevent_sleep_mode():
    system = platform.system()
    proc = None

    try:
        if system == "Windows":
            # Prevent sleep & display off
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)

        elif system == "Darwin":  # macOS
            # Use built-in caffeinate command
            proc = subprocess.Popen(["caffeinate"])

        elif system == "Linux":
            # Use systemd-inhibit to prevent sleep
            proc = subprocess.Popen(
                ["systemd-inhibit", "--why=LibPack build", "--mode=block", "sleep", "infinity"]
            )

        yield

    finally:
        if system == "Windows":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        elif proc:
            proc.terminate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Builds a collection of FreeCAD dependencies for the current system"
    )
    parser.add_argument(
        "-m",
        "--mode",
        help="'release' or 'debug''",
        default="release",
    )
    parser.add_argument(
        "-c",
        "--config",
        help="Path to a JSON configuration file for this utility",
        default="./config.json",
    )
    parser.add_argument(
        "-e",
        "--no-skip-existing-clone",
        action="store_false",
        help="If a given clone (or download) directory exists, delete it and download it again",
    )
    parser.add_argument(
        "-b",
        "--no-skip-existing-build",
        action="store_false",
        help="If a given build already exists, run the build process again anyway",
    )
    parser.add_argument(
        "-s",
        "--silent",
        action="store_true",
        help="I kow what I'm doing, don't ask me any questions",
    )
    parser.add_argument(
        "-z",
        "--archive",
        action="store_true",
        help=(
            "After the build completes, compress the finished LibPack directory into a "
            "sibling .7z archive suitable for distribution."
        ),
    )
    parser.add_argument("--7zip", help="Path to 7-zip executable", default=path_to_7zip)
    parser.add_argument("--bison", help="Path to Bison executable", default=path_to_bison)
    parser.add_argument(
        "--vs-version",
        help=(
            "Visual Studio toolchain to build with. Accepts 'latest' (default), "
            "'2022', '2026', or a raw vswhere -version range such as '[17.0,18.0)'."
        ),
        default="latest",
    )
    parser.add_argument(
        "--vcvars-ver",
        help=(
            "Optional MSVC toolset version to select inside the chosen Visual Studio "
            "install, passed to vcvars64.bat as -vcvars_ver=VALUE. Use this to build "
            "with the v143 (VS 2022) toolset from a VS 2026 installation, for example "
            "--vcvars-ver=14.4."
        ),
        default="",
    )
    parser.add_argument(
        "--fallback-build-dir",
        help=(
            "Override the fallback build directory used by Qt to dodge Windows path-length "
            "limits during its build. Replaces the value declared in config.json for the qt "
            "entry. Supply a short path on a drive that exists on this machine, for example "
            "C:\\temp."
        ),
        default="",
    )
    parser.add_argument("path-to-final-libpack-dir", nargs="?", default="./")
    args = vars(parser.parse_args())

    config_dict = load_config(args["config"])
    if args["fallback_build_dir"]:
        for item in config_dict.get("content", []):
            if item.get("name") == "qt":
                item["fallback-build-dir"] = args["fallback_build_dir"]
                break
    path_to_7zip = args["7zip"]
    path_to_bison = args["bison"]

    mode = (
        compile_all.BuildMode.DEBUG
        if args["mode"].lower() == "debug"
        else compile_all.BuildMode.RELEASE
    )
    working = compile_all.working_dir_name(mode)
    os.makedirs(working, exist_ok=True)
    os.chdir(working)
    if args["no_skip_existing_clone"]:
        dirname = compile_all.libpack_dir(config_dict, mode)
        if not os.path.exists(dirname):
            base = create_libpack_dir(config_dict, mode)
        else:
            base = dirname
    else:
        base = create_libpack_dir(config_dict, mode)
    with prevent_sleep_mode():
        fetch_remote_data(config_dict, mode, args["no_skip_existing_clone"])

        compiler = compile_all.Compiler(
            config_dict,
            bison_path=path_to_bison,
            skip_existing=args["no_skip_existing_build"],
            mode=mode,
        )
        vs_install_path = subprocess.check_output(
            build_vswhere_args(args["vs_version"]),
            text=True,
        ).strip()
        if not vs_install_path:
            print(
                f"ERROR: vswhere returned no Visual Studio installation matching "
                f"--vs-version={args['vs_version']!r}"
            )
            exit(1)

        base_path = Path(vs_install_path) / "VC" / "Auxiliary" / "Build"
        if platform.machine() == "ARM64":
            init_bat = str(base_path / "vcvarsarm64.bat")
        else:
            init_bat = str(base_path / "vcvars64.bat")
        # vcvars internally shells out to vswhere.exe to enumerate installed MSVC tool
        # versions. If vswhere is not on PATH, vcvars silently ignores -vcvars_ver and
        # falls back to the latest installed compiler. Prepend the vswhere directory to
        # PATH so child subprocesses inherit it and -vcvars_ver is honored.
        vswhere_dir = os.path.dirname(vswhere)
        if vswhere_dir and vswhere_dir not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = vswhere_dir + os.pathsep + os.environ.get("PATH", "")
        if args["vcvars_ver"]:
            compiler.init_script = [init_bat, f"-vcvars_ver={args['vcvars_ver']}"]
            compiler.msvc_tools_version = resolve_msvc_tools_version(
                vs_install_path, args["vcvars_ver"]
            )
            if not compiler.msvc_tools_version:
                print(
                    f"ERROR: No installed MSVC tools matching --vcvars-ver={args['vcvars_ver']!r} "
                    f"under {vs_install_path}\\VC\\Tools\\MSVC. Available: "
                    f"{list_msvc_tools_versions(vs_install_path)}"
                )
                exit(1)
        else:
            compiler.init_script = [init_bat]
        compiler.compile_all()

        # Final cleanup: delete extraneous files and remove local path references from the cMake files
        base_path = compile_all.libpack_dir(config_dict, mode)
        if mode == compile_all.BuildMode.DEBUG:
            extra_pdb_search_dirs = [
                item["fallback-build-dir"]
                for item in config_dict.get("content", [])
                if "fallback-build-dir" in item
            ]
            path_cleaner.install_pdb_sidecars(base_path, os.getcwd(), extra_pdb_search_dirs)
        path_cleaner.delete_extraneous_files(base_path)
        path_cleaner.remove_local_path_from_cmake_files(base_path)
        path_cleaner.correct_opencascade_freetype_ref(base_path)
        path_cleaner.delete_qtwebengine(base_path)
        # path_cleaner.delete_qtquick(base_path)
        path_cleaner.delete_llvm_executables(base_path)
        path_cleaner.delete_clang_executables(base_path)
        path_cleaner.delete_unused_static_libs(base_path)
        path_cleaner.delete_lldb(base_path)
        path_cleaner.delete_bundled_cmake(base_path)
        path_cleaner.delete_llvm_internal_headers(base_path)
        path_cleaner.delete_documentation(base_path)
        path_cleaner.delete_occt_sample_data(base_path)
        path_cleaner.delete_python_test_suites(base_path)
        # if mode == compile_all.BuildMode.RELEASE:
        #     path_cleaner.delete_pdb_files(base_path)

        write_manifest(config_dict, mode)

        if args["archive"]:
            create_archive(base_path)
