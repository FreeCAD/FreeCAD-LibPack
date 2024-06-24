#!/bin/python3
import sys

# SPDX-License-Identifier: LGPL-2.1-or-later

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
import json
import os
import shutil
import stat
import subprocess
from urllib.parse import urlparse
import path_cleaner

try:
    import requests
except ImportError:
    print("Please pip --install requests")
    exit(1)

try:
    import diff_match_patch
except ImportError:
    print("Please pip --install diff-match-patch")
    exit(1)

import compile_all

path_to_7zip = "C:\\Program Files\\7-Zip\\7z.exe"
path_to_bison = "C:\\Program Files\\win_flex_bison\\win_bison.exe"
devel_init_script = "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Auxiliary\\Build\\vcvars64.bat"


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


def fetch_remote_data(config: dict, skip_existing: bool = False):
    """Clone the required repos and download the URLs"""
    content = config["content"]
    for item in content:
        if skip_existing and os.path.exists(item["name"]):
            continue
        if "git-repo" in item and "git-ref" in item:
            clone(item["name"], item["git-repo"], item["git-ref"])
        elif "git-repo" in item:
            clone(item["name"], item["git-repo"])
        elif "git-ref" in item:
            print(f"ERROR: found a git ref without a git repo for {item['name']}")
            exit()
        elif "url" in item:
            download(item["name"], item["url"])
        else:
            # Just make the directory, presumably later code will know what to do
            os.makedirs(item["name"], exist_ok=True)
        if "patches" in item:
            cwd = os.getcwd()
            os.chdir(item["name"])
            compile_all.patch_files(item["patches"])
            os.chdir(cwd)


def clone(name: str, url: str, ref: str = None):
    """Shallow clones a git repo at the given ref using a system-installed git"""
    try:
        if ref is None:
            print(f"Cloning {url}")
        else:
            print(f"Cloning {url} at {ref}")
        args = ["git", "clone"]
        if ref is not None:
            args.extend(["--branch", ref])
        args.extend(["--depth", "1", "--recurse-submodules", url, name])
        subprocess.run(args, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: failed to clone git repo {url} at ref {ref}")
        print(e.output)
        exit(e.returncode)


def download(name: str, url: str):
    """Directly downloads some sort of compressed format file and decompresses it using a system-installed 7-zip"""
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
    try:
        subprocess.run([path_to_7zip, "x", filename], capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        print("ERROR: failed to unzip {filename} at from {name} using {path_to_7zip}")
        print(e.output)
        exit(e.returncode)
    os.chdir(original_dir)


def write_manifest(outer_config: dict, mode_used: compile_all.BuildMode):
    manifest_file = os.path.join(compile_all.libpack_dir(outer_config, mode_used), "manifest.json")
    with open(manifest_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(outer_config["content"], indent="    "))
    version_file = os.path.join(
        compile_all.libpack_dir(outer_config, mode_used), "FREECAD_LIBPACK_VERSION"
    )
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(outer_config["LibPack-version"])


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
        "-w",
        "--working",
        help="Directory to put all the clones and downloads in",
        default="./working",
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
    parser.add_argument("--7zip", help="Path to 7-zip executable", default=path_to_7zip)
    parser.add_argument("--bison", help="Path to Bison executable", default=path_to_bison)
    parser.add_argument("path-to-final-libpack-dir", nargs="?", default="./")
    args = vars(parser.parse_args())

    config_dict = load_config(args["config"])
    path_to_7zip = args["7zip"]
    path_to_bison = args["bison"]

    os.makedirs("working", exist_ok=True)
    os.chdir("working")
    mode = (
        compile_all.BuildMode.DEBUG
        if args["mode"].lower() == "debug"
        else compile_all.BuildMode.RELEASE
    )
    if args["no_skip_existing_clone"]:
        dirname = compile_all.libpack_dir(config_dict, mode)
        if not os.path.exists(dirname):
            base = create_libpack_dir(config_dict, mode)
        else:
            base = dirname
    else:
        base = create_libpack_dir(config_dict, mode)

    fetch_remote_data(config_dict, args["no_skip_existing_clone"])

    compiler = compile_all.Compiler(
        config_dict,
        bison_path=path_to_bison,
        skip_existing=args["no_skip_existing_build"],
        mode=mode,
    )
    compiler.init_script = devel_init_script
    compiler.compile_all()

    # Final cleanup: delete extraneous files and remove local path references from the cMake files
    base_path = compile_all.libpack_dir(config_dict, mode)
    path_cleaner.delete_extraneous_files(base_path)
    path_cleaner.remove_local_path_from_cmake_files(base_path)
    path_cleaner.correct_opencascade_freetype_ref(base_path)

    write_manifest(config_dict, mode)
