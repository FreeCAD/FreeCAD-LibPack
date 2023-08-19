#!/bin/python3

# SPDX-License-Identifier: LGPL-2.1-or-later

# Every package has its own compilation and installation idiosyncrasies, so we have to use a custom
# build script for each one.

from enum import Enum
import os
import subprocess
import sys


class build_mode(Enum):
    DEBUG = 1
    RELEASE = 2


def compile_all(config: dict, mode: build_mode):
    content = config["content"]
    base_dir = os.curdir
    create_libpack_dir(config, mode)
    for item in content:
        # All build methods are named using "build_XXX" where XXX is the name of the package in the config file
        os.chdir(item["name"])
        build_function_name = "build_" + item["name"]
        build_function = globals()[build_function_name]
        build_function(mode)
        os.chdir(base_dir)


def create_libpack_dir(config: dict, mode: build_mode) -> str:
    """Create a new directory for this LibPack compilation, using the version of FreeCAD, the version of
    the LibPack, and whether it's in release or debug mode. Returns the name of the created directory.
    """

    dirname = "LibPack-{}-v{}-{}".format(
        config["FreeCAD-version"],
        config["LibPack-version"],
        "release" if mode == build_mode.RELEASE else "debug",
    )
    if os.path.exists(dirname):
        backup_name = dirname + "-backup-" + "a"
        while os.path.exists(backup_name):
            if backup_name[-1] =="z":
                print(
                    "You have too many old LibPack backup directories. Please delete some of them."
                )
                exit(1)
            backup_name = backup_name[:-1] + chr(ord(backup_name[-1]) + 1)
            
        os.rename(dirname, backup_name)
    os.mkdir(dirname)
    return dirname

def build_nonexistent(mode: build_mode):
    """ Used for automated testing to allow Mock injection """
    pass

def build_python(mode: build_mode):
    if sys.platform.startswith("win32"):
        subprocess.run(
            [
                "PCbuild\\build.bat",
                "-p",
                "x64",
                "-c",
                "Release" if mode == build_mode.RELEASE else "Debug",
            ],
            check=True,
        )
    else:
        raise NotImplemented("Non-windows compilation of Python is not implemented yet")
