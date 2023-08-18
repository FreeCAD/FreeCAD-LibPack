#!/bin/python3

# SPDX-License-Identifier: LGPL-2.1-or-later

# Prerequisites:
#   * A working compiler toolchain for your system, accessible by cMake
#   * CMake 
#   * git
#   * 7-zip
#   * Some version of Python that can run this file
#   * Network access
#   * Qt - the base installation plus Qt Image Formats, Qt Webengine, Qt Webview, and Qt PDF

import argparse
import json
import os
import shutil
import subprocess

CONFIG = {}

def delete_existing(path:str, silent:bool=False):
    """ Delete a directory tree, with optional confirmation sequence """
    do_it = silent
    if not silent and os.path.exists(path):
        response = input(f"Really delete entire path {path}? y/N ")
        if response.lower() == "y":
            do_it = True
    if do_it:
        print("Removing {path} prior to beginning")
        shutil.rmtree(path)
    else:
        print("NOT removing {path}")

def load_config(path:str):
    """ Load a JSON-formatted configuration file for this utility """
    with open (path, "r", encoding="utf-8") as f:
        config_data = f.read()
        CONFIG = json.loads(config_data)

def clone_git_repos():
    """ Clone the required repos """
    content = CONFIG["content"]
    for item in content:
        if "git-repo" in item and "git-ref" in item:
            clone(item["git-repo"], item["git-ref"])

def clone(url:str, ref:str):
    """ Shallow clones a git repo at the given ref using a system-installed git """
    try:
        run_result = subprocess.run([
            "git",
            "clone",
            "--branch",
            ref,
            "--depth",
            "1",
            url,
        ], capture_output=True)
    except subprocess.CalledProcessError as e:
        print("ERROR: FAILED TO CLONE REPO {url} AT REF {ref}")
        print(e.output)
        exit(e.returncode)

def download(url:str):
    """ Directly downloads some sort of compressed format file and decompresses it using a system-installed 7-zip"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Builds a collection of FreeCAD dependencies for the current system"
    )
    parser.add_argument("-c", "--config", help="Path to a JSON configuration file for this utility", default="./config.json")
    parser.add_argument("-w", "--working", help="Directory to put all the clones and downloads in", default="./working")
    parser.add_argument("-e", "--skip-existing-clone", action='store_true', help="If a given clone (or download) directory exists, skip cloning/downloading")
    parser.add_argument("-b", "--skip-existing-build", action='store_true', help="If a given build directory exists, skip building")
    parser.add_argument("-s", "--silent", action='store_true', help="I kow what I'm doing, don't ask me any questions")
    parser.add_argument("path-to-final-libpack-dir", nargs='?', default="./")
    args = vars(parser.parse_args())

    if not args["skip_existing_clone"]:
        delete_existing(args["working"], silent=args["silent"])

    os.makedirs(args["working"], exist_ok=True)
    os.chdir(args["working"])
    load_config(args["config"])

    clone_git_repos()


