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


class BuildMode(Enum):
    DEBUG = 1
    RELEASE = 2

    def __str__(self) -> str:
        if self == BuildMode.DEBUG:
            return "Debug"
        elif self == BuildMode.RELEASE:
            return "Release"
        else:
            return "Unknown"


class Compiler:
    def __init__(self, config, mode, bison_path, skip_existing: bool = False):
        self.config = config
        self.mode = mode
        self.bison_path = bison_path
        self.base_dir = os.getcwd()
        self.skip_existing = skip_existing
        libpack_dir = "LibPack-{}-v{}-{}".format(
            config["FreeCAD-version"],
            config["LibPack-version"],
            str(mode),
        )
        self.install_dir = os.path.join(os.getcwd(), libpack_dir)

    def compile_all(self):
        for item in self.config["content"]:
            # All build methods are named using "build_XXX" where XXX is the name of the package in the config file
            print(f"Building {item['name']} in {self.mode} mode")
            os.chdir(item["name"])
            build_function_name = "build_" + item["name"]
            build_function = getattr(self, build_function_name)
            build_function(item)
            os.chdir(self.base_dir)

    def build_nonexistent(self, _=None):
        """Used for automated testing to allow easy Mock injection"""

    def build_python(self, _=None):
        """ NOTE: This doesn't install correctly, so should not be used at this time... install Python manually """
        if sys.platform.startswith("win32"):
            expected_exe_path = os.path.join(self.install_dir, "bin", "python.exe")
            if self.skip_existing and os.path.exists(expected_exe_path):
                print("Not rebuilding, instead just using existing Python in the LibPack installation path")
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
                print(e.output.decode("utf-8"))
                exit(e.returncode)
            bin_dir = os.path.join(self.install_dir, "bin")
            lib_dir = os.path.join(bin_dir, "Lib")
            inc_dir = os.path.join(bin_dir, "Include")
            os.makedirs(bin_dir, exist_ok=True)
            os.makedirs(lib_dir, exist_ok=True)
            os.makedirs(bin_dir, exist_ok=True)
            shutil.copytree(f"PCBuild\\{path}", bin_dir, dirs_exist_ok=True)
            shutil.copytree(f"Lib", lib_dir, dirs_exist_ok=True)
            shutil.copytree(f"Include", inc_dir, dirs_exist_ok=True)
        else:
            raise NotImplemented(
                "Non-Windows compilation of Python is not implemented yet"
            )

    def get_python_version(self) -> str:
        path_to_python = os.path.join(self.install_dir, "bin", "python")
        if sys.platform.startswith("win32"):
            path_to_python += ".exe"
        try:
            result = subprocess.run([path_to_python, "--version"], capture_output=True, check=True)
            _, _, version_number = result.stdout.decode("utf-8").strip().partition(" ")
            components = version_number.split(".")
            python_version = f"{components[0]}.{components[1]}"
            return python_version
        except subprocess.CalledProcessError as e:
            print("ERROR: Failed to run LibPack's Python executable")
            print(e.output.decode("utf-8"))
            exit(1)

    def build_qt(self, options: dict):
        """Doesn't really "build" Qt, just copies the pre-compiled libraries from the configured path"""
        qt_dir = options["install-directory"]
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "metatypes")):
                print("Not re-copying, instead just using existing Qt in the LibPack installation path")
                return
        if not os.path.exists(qt_dir):
            print(f"Error: specified Qt installation path does not exist ({qt_dir})")
            exit(1)
        shutil.copytree(qt_dir, self.install_dir, dirs_exist_ok=True)

    def build_boost(self, _=None):
        """ Builds boost shared libraries and installs libraries and headers """
        if self.skip_existing:
            if os.path.exists(os.path.join(self.install_dir, "include", "boost")):
                print("Not rebuilding boost, it is already in the LibPack")
                return
        # Boost uses a custom build system and needs a config file to find our Python
        with open(os.path.join("tools", "build", "src", "user-config.jam"), "w", encoding="utf-8") as user_config:
            exe = os.path.join(self.install_dir, "bin", "python")
            if sys.platform.startswith("win32"):
                exe += ".exe"
            inc_dir = os.path.join(self.install_dir, "bin", "Include")
            lib_dir = os.path.join(self.install_dir, "bin", "Lib")
            python_version = self.get_python_version()
            print(f"Building boost-python with Python {python_version}")
            user_config.write(f'using python : {python_version} : "{exe}" : "{inc_dir}" : "{lib_dir}"  ;\n')
        try:
            subprocess.run(["bootstrap.bat"], capture_output=True, check=True)
            subprocess.run(["b2", f"variant={str(self.mode).lower()}"], check=True, capture_output=True)
            shutil.copytree(os.path.join("stage", "lib"), os.path.join(self.install_dir, "lib"), dirs_exist_ok=True)
            shutil.copytree("boost", os.path.join(self.install_dir, "include", "boost"),
                            dirs_exist_ok=True)
        except subprocess.CalledProcessError as e:
            print("Error: failed to build boost")
            print(e.output.decode("utf-8"))
            exit(e.returncode)
