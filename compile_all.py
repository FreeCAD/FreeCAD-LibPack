#!/bin/python3

# SPDX-License-Identifier: LGPL-2.1-or-later

# Every package has its own compilation and installation idiosyncrasies, so we have to use a custom
# build script for each one.

from enum import Enum
import os
import platform
import shutil
import subprocess
import sys


class build_mode(Enum):
    DEBUG = 1
    RELEASE = 2

    def __str__(self) -> str:
        if self == build_mode.DEBUG:
            return "Debug"
        elif self == build_mode.RELEASE:
            return "Release"
        else:
            return "Unknown"


class Compiler:
    def __init__(self, config, mode, bison_path, skip_existing:bool=False):
        self.config = config
        self.mode = mode
        self.bison_path = bison_path
        self.base_dir = os.getcwd()
        self.skip_existing = skip_existing

    def compile_all(self):
        content = self.config["content"]
        libpack_dir = self.create_libpack_dir()
        self.install_dir = os.path.join(os.getcwd(), libpack_dir)
        for item in content:
            # All build methods are named using "build_XXX" where XXX is the name of the package in the config file
            print(f"Building {item['name']} in {self.mode} mode")
            os.chdir(item["name"])
            build_function_name = "build_" + item["name"]
            build_function = getattr(self, build_function_name)
            build_function(item)
            os.chdir(self.base_dir)

    def create_libpack_dir(self) -> str:
        """Create a new directory for this LibPack compilation, using the version of FreeCAD, the version of
        the LibPack, and whether it's in release or debug mode. Returns the name of the created directory.
        """

        dirname = "LibPack-{}-v{}-{}".format(
            self.config["FreeCAD-version"],
            self.config["LibPack-version"],
            str(self.mode),
        )
        if os.path.exists(dirname) and not self.skip_existing:
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
        return dirname

    def build_nonexistent(self, options=None):
        """Used for automated testing to allow easy Mock injection"""

    def build_python(self, options=None):
        if sys.platform.startswith("win32"):
            if self.skip_existing:
                if os.path.exists(os.path.join(self.install_dir,"bin","python.exe")):
                    print("Skipping existing Python")
                    return
            try:
                arch = "x64" if platform.machine() == "AMD64" else "ARM64"
                path = "amd64" if platform.machine() == "AMD64" else "arm64"
                subprocess.run(
                    [
                        "PCbuild\\build.bat",
                        "-p",
                        arch,
                        "-c",
                        str(self.mode),
                    ],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                print("Python build failed")
                print(e.output)
                exit(e.returncode)
            bin_dir = os.path.join(self.install_dir, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            shutil.copytree(f"PCBuild\\{path}", bin_dir, dirs_exist_ok=True)
        else:
            raise NotImplemented(
                "Non-windows compilation of Python is not implemented yet"
            )

    def build_qt(self, options:dict):
        """Doesn't really "build" Qt, just copies the pre-compiled libraries from the configured path"""
        qt_dir = options["install-directory"]
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir,"metatypes")):
                print("Skipping existing Qt")
                return
        if not os.path.exists(qt_dir):
            print(f"Error: specified Qt installation path does not exist ({qt_dir})")
            exit(1)
        shutil.copytree(qt_dir, self.install_dir, dirs_exist_ok=True)

    def build_boost(self, options:dict=None):
        """ Builds boost shared libraries and installs libraries and headers """
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir,"include","boost")):
                print("Skipping existing boost")
                return
        # Boost uses a custom build system and needs a config file