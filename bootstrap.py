#!/bin/python3

# SPDX-License-Identifier: LGPL-2.1-or-later

import json
import os
import sys

from compile_all import BuildMode

"""  """

def parse_args() -> dict:
    if len(sys.argv) > 3:
        usage()
        exit(1)
    new_config_dict = {"mode": BuildMode.RELEASE, "config_file": "config.json"}
    for arg in sys.argv[1:]:
        key, value = extract_arg(arg)
        new_config_dict[key] = value
    return new_config_dict


def extract_arg(arg) -> tuple[str, object]:
    if arg.lower() in ["release", "debug"]:
        return "mode", BuildMode.RELEASE if arg.lower() == "release" else BuildMode.DEBUG
    return "config_file", arg


def usage():
    print("Used to create the base LibPack directory that you will then manually install Python into")
    print("Usage: python bootstrap.py [config_file] [release|debug]")
    print()
    print('Result: A new working/LibPack-XX-YY-MM directory has been created')
    print('Next step: install Python into working/LibPack-XX-YY-MM/bin, then run:')
    print('  .\\working\\LibPack-XX-YY-MM\\bin\\python create_libpack.py')
    print('(where XX, YY, and MM change according to the config and inputs)')


def create_libpack_dir(config: dict, mode: BuildMode) -> str:
    """Create a new directory for this LibPack compilation, using the version of FreeCAD, the version of
    the LibPack, and whether it's in release or debug mode. Returns the name of the created directory.
    """

    dirname = "LibPack-{}-v{}-{}".format(
        config["FreeCAD-version"],
        config["LibPack-version"],
        str(mode),
    )
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
    return dirname


if __name__ == "__main__":
    args = parse_args()
    with open(args["config_file"], "r", encoding="utf-8") as f:
        config_data = f.read()
    config_dict = json.load(config_data)
    os.makedirs("working", exist_ok=True)
    os.chdir("working")
    create_libpack_dir(config_dict, args["mode"])
