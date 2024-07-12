#  What I really want to do is clean for release. So replace explicit paths with references to CMAKE_CURRENT_SOURCE_DIR
# in cMake files, and also delete some extra files that are spewed out by various installers. The various licenses
# should probably be consolidated.

import os
import shutil

paths_to_delete = [
    "custom_vc14_64.bat",
    "custom.bat",
    "USING_HDF5_CMake.txt",
    "USING_HDF5_VS.txt",
    "env.bat",
    "draw.bat",
    "RELEASE.txt",
    "samples",
]


def delete_extraneous_files(base_path: str) -> None:
    """Delete each of the files listed above from the path specified in base_path. Failure to delete a file does not
    constitute a fatal error."""
    print("Removing extraneous files")
    if not os.path.exists(base_path):
        raise RuntimeError(f"{base_path} does not exist")
    if not os.path.isdir(base_path):
        raise RuntimeError(f"{base_path} is not a directory")
    for file in paths_to_delete:
        try:
            os.unlink(os.path.join(base_path, file))
        except OSError as e:
            # If the file isn't there, that's as good as deleting it, right?
            pass


def remove_local_path_from_cmake_files(base_path: str) -> None:
    """In many cases, the local compilation paths get stored into the cMake files. They should not ever be used, but
    a) OpenCASCADE codes in the local path to FreeType, which then fails when the LibPack is distributed, and b) for
    good measure cMake files shouldn't refer to non-existent paths on a foreign system. So this method looks for
    cmake config files and cleans the ones it finds."""
    print("Removing local paths from cMake files")
    for root, dirs, files in os.walk(base_path):
        for file in files:
            if file.lower().endswith(".cmake"):
                remove_local_path_from_cmake_file(base_path, os.path.join(root, file))


def remove_local_path_from_cmake_file(base_path: str, file_to_clean: str) -> None:
    """Modify a cMake file to remove base_path and replace it with ${CMAKE_CURRENT_SOURCE_DIR} -- WARNING: effectively
    edits the file in-place, no backup is made."""
    depth_string = create_depth_string(base_path, file_to_clean)
    with open(file_to_clean, "r", encoding="UTF-8") as f:
        contents = f.read()

    if base_path.endswith(os.path.sep):
        base_path = base_path[: -len(os.path.sep)]

    # First, just replace the exact string we were given
    contents = contents.replace(
        base_path, "${CMAKE_CURRENT_SOURCE_DIR}/" + depth_string[:-1]
    )  # Skip the final /

    # Most occurrences should NOT have been the exact string if we are on Windows, since cMake paths should always
    # use forward slashes, so make sure to do that replacement as well
    if os.pathsep != "/":
        cmake_base_path = base_path.replace(
            os.path.sep, "/"
        )  # cMake paths should always use forward slash
        contents = contents.replace(
            cmake_base_path, "${CMAKE_CURRENT_SOURCE_DIR}/" + depth_string[:-1]
        )  # Skip /
    with open(file_to_clean, "w", encoding="utf-8") as f:
        f.write(contents)


def create_depth_string(base_path: str, file_to_clean: str) -> str:
    """Given a base path and a file, determine how many "../" must be appended to the file's containing directory
    to result in a path that resolves to base_path. Returns a string containing just some number of occurrences of
    "../" e.g. "../../../" to move up three levels from file_to_clean's containing folder."""

    file_to_clean = os.path.normpath(file_to_clean)
    if not file_to_clean.startswith(base_path):
        raise RuntimeError(f"{file_to_clean} does not appear to be in {base_path}")

    if base_path.endswith(os.path.sep):
        base_path = base_path[: -len(os.path.sep)]

    containing_directory = os.path.dirname(file_to_clean)
    directories_to_file = len(containing_directory.split(os.path.sep))
    directories_in_base = len(base_path.split(os.path.sep))
    num_steps_up = directories_to_file - directories_in_base
    return "../" * num_steps_up  # For use in cMake, so always a forward slash here


def correct_opencascade_freetype_ref(base_path: str):
    """OpenCASCADE hardcodes the path to the freetype it was compiled against. The above code doesn't correct it to
    the necessary path because of the way this variable is used within cMake. So just remove the path altogether and
    rely on the rest of our configuration to find the correct one."""
    files_to_fix = ["OpenCASCADEDrawTargets.cmake", "OpenCASCADEVisualizationTargets.cmake"]
    for fix in files_to_fix:
        path = os.path.join(base_path, "cmake", fix)
        with open(path, "r", encoding="utf-8") as f:
            contents = f.read()
        contents = contents.replace(
            "${CMAKE_CURRENT_SOURCE_DIR}/../lib/freetype.lib", "freetype.lib"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(contents)


def delete_qtwebengine(base_path: str):
    """QtWebEngine is huge and pervasive -- it's also not used by FreeCAD (anymore). Delete anything that seems to be
    related to it from the LibPack."""

    print("Removing QtWebEngine (and related code)")
    for root, dirs, files in os.walk(base_path):
        for dir in dirs:
            if (
                "webengine" in dir.lower()
                or "webchannel" in dir.lower()
                or "websockets" in dir.lower()
            ):
                try:
                    full_path = os.path.join(root, dir)
                    shutil.rmtree(full_path)
                except OSError as e:
                    print(f"Failed to delete file {full_path}: {e}")
        for file in files:
            if "webengine" in file.lower() or "webchannel" in file.lower():
                try:
                    full_path = os.path.join(root, file)
                    os.unlink(full_path)
                except OSError as e:
                    print(f"Failed to delete path {full_path}: {e}")


def delete_qtquick(base_path: str):
    """QtQuick is unused in FreeCAD at this time."""

    def is_qtquick(name: str) -> bool:
        lc = name.lower()
        if "qtquick" in lc or "qml" in lc:
            return True
        if lc.startswith("q") and "quick" in lc:
            return True
        return False

    print("Removing QtQuick/QML")
    for root, dirs, files in os.walk(base_path):
        for dir in dirs:
            if is_qtquick(dir):
                try:
                    full_path = os.path.join(root, dir)
                    shutil.rmtree(full_path)
                except OSError as e:
                    print(f"Failed to delete file {full_path}: {e}")
        for file in files:
            if is_qtquick(file):
                try:
                    full_path = os.path.join(root, file)
                    os.unlink(full_path)
                except OSError as e:
                    print(f"Failed to delete path {full_path}: {e}")


def delete_llvm_executables(base_path: str):
    """During the build of the libpack, a number of llvm executable files are created: these are not needed to compile
    or run FreeCAD, so remove them."""
    print("Removing llvm executables")
    files_in_bin = os.listdir(os.path.join(base_path, "bin"))
    for file in files_in_bin:
        if file.startswith("llvm") and file.endswith(".exe"):
            try:
                os.unlink(os.path.join(base_path, "bin", file))
            except OSError as e:
                pass


def delete_clang_executables(base_path: str):
    """During the build of the libpack, a number of clang executable files are created: these are not needed to compile
    or run FreeCAD, so remove them."""
    print("Removing clang executables")
    files_in_bin = os.listdir(os.path.join(base_path, "bin"))
    for file in files_in_bin:
        if file.startswith("clang") and file.endswith(".exe"):
            try:
                os.unlink(os.path.join(base_path, "bin", file))
            except OSError as e:
                pass
