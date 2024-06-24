# SPDX-License-Identifier: LGPL-2.1-or-later

import diff_match_patch
import sys


def print_usage():
    print(
        "Generate a patchfile that can be used with the create_libpack.py script to patch source files"
    )
    print("Usage: python generate_patch.py original_file corrected_file output_patch_file")


def parse_args():
    if len(sys.argv) != 4:
        print_usage()
        exit(1)


def generate_patch(old, new) -> str:
    dmp = diff_match_patch.diff_match_patch()
    patches = dmp.patch_make(old, new)
    return dmp.patch_toText(patches)


def run(old_file, new_file, output_file):
    with open(old_file, "r", encoding="utf-8") as f:
        old = f.read()
    with open(new_file, "r", encoding="utf-8") as f:
        new = f.read()
    patch = generate_patch(old, new)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"@@@ {old_file} @@@\n")
        f.write(patch)


if __name__ == "__main__":
    parse_args()
    run(sys.argv[1], sys.argv[2], sys.argv[3])
