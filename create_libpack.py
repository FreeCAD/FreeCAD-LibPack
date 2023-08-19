#!/bin/python3

# SPDX-License-Identifier: LGPL-2.1-or-later

# Prerequisites:
#   * Network access
#   * A working compiler toolchain for your system, accessible by cMake
#   * CMake
#   * git
#   * 7z (see https://www.7-zip.org)
#   * Some version of Python that can run this file
#   * The "requests" Python package (e.g. 'pip install requests')
#   * Qt - the base installation plus Qt Image Formats, Qt Webengine, Qt Webview, and Qt PDF
#   * GNU Bison (for Windows see https://github.com/lexxmark/winflexbison/)

import argparse
import json
import os
import shutil
import stat
import subprocess
import requests
from urllib.parse import urlparse

import compile_all

path_to_7zip = "C:\\Program Files\\7-Zip\\7z.exe"
path_to_bison = "C:\\Program Files\\win_flex_bison\\win_bison.exe"


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Builds a collection of FreeCAD dependencies for the current system"
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
        "--skip-existing-clone",
        action="store_true",
        help="If a given clone (or download) directory exists, skip cloning/downloading",
    )
    parser.add_argument(
        "-b",
        "--skip-existing-build",
        action="store_true",
        help="If a given build directory exists, skip building",
    )
    parser.add_argument(
        "-s",
        "--silent",
        action="store_true",
        help="I kow what I'm doing, don't ask me any questions",
    )
    parser.add_argument("--7zip", help="Path to 7-zip executable", default=path_to_7zip)
    parser.add_argument(
        "--bison", help="Path to Bison executable", default=path_to_bison
    )
    parser.add_argument("path-to-final-libpack-dir", nargs="?", default="./")
    args = vars(parser.parse_args())

    config = load_config(args["config"])
    path_to_7zip = args["7zip"]
    path_to_bison = args["bison"]

    if not args["skip_existing_clone"]:
        delete_existing(args["working"], silent=args["silent"])

    os.makedirs(args["working"], exist_ok=True)
    os.chdir(args["working"])

    fetch_remote_data(config, args["skip_existing_clone"])

    compiler = compile_all.Compiler(
        config,
        compile_all.BuildMode.RELEASE,
        bison_path=path_to_bison,
        skip_existing=args["skip_existing_build"],
    )
    compiler.compile_all()


# Preliminary setup that will be needed for running CMake
# CMAKE_PREFIX_PATH to libpack dir
# CMAKE_INSTALL_PREFIX to libpack dir
