#  What I really want to do is clean for release. So replace explicit paths with references to CMAKE_CURRENT_SOURCE_DIR
# in cMake files, and also delete some extra files that are spewed out by various installers. The various licenses
# should probably be consolidated.

import os

paths_to_delete = [
    "custom_vc14_64.bat",
    "custom.bat",
    "USING_HDF5_CMake.txt",
    "USING_HDF5_VS.txt",
    "env.bat",
    "draw.bat",
    "RELEASE.txt",
    ]


def delete_extraneous_files(base_path: str) -> None:
    """Delete each of the files listed above from the path specified in base_path. Failure to delete a file does not
     constitute a fatal error."""
    if not os.path.exists(base_path):
        raise RuntimeError(f"{base_path} does not exist")
    if not os.path.isdir(base_path):
        raise RuntimeError(f"{base_path} is not a directory")
    for file in paths_to_delete:
        try:
            os.unlink(os.path.join(base_path, file))
        except OSError as e:
            print(e)


def remove_local_path_from_cmake_files(base_path: str) -> None:
    """In many cases, the local compilation paths get stored into the cMake files. They should not ever be used, but
    a) OpenCASCADE codes in the local path to FreeType, which then fails when the LibPack is distributed, and b) for
    good measure cMake files shouldn't refer to non-existent paths on a foreign system. So this method looks for
    cmake config files and cleans the ones it finds."""
    for root, dirs, files in os.walk(base_path):
        for file in files:
            if file.lower().endswith(".cmake"):
                remove_local_path_from_cmake_file(base_path, os.path.join(root, file))


def remove_local_path_from_cmake_file(base_path: str, file_to_clean: str) -> None:
    """ Modify a cMake file to remove base_path and replace it with ${CMAKE_CURRENT_SOURCE_DIR} -- WARNING: effectively
    edits the file in-place, no backup is made."""
    depth_string = create_depth_string(base_path, file_to_clean)
    with open(file_to_clean, "r", encoding="UTF-8") as f:
        contents = f.read()
    contents.replace(base_path, "${CMAKE_CURRENT_SOURCE_DIR}/" + depth_string)
    with open(file_to_clean, "w", encoding="utf-8") as f:
        f.write(contents)


def create_depth_string(base_path: str, file_to_clean: str) -> str:
    """Given a base path and a file, determine how many "../" must be appended to the file's containing directory
    to result in a path that resolves to base_path. Returns a string containing just some number of occurrences of
    "../" e.g. "../../../" to move up three levels from file_to_clean's containing folder."""
    if not file_to_clean.startswith(base_path):
        raise RuntimeError(f"{file_to_clean} does not appear to be in {base_path}")

    containing_directory = os.path.dirname(file_to_clean)
    directories_to_file = len(containing_directory.split(os.path.sep))
    directories_in_base = len(base_path.split(os.path.sep))
    num_steps_up = directories_to_file - directories_in_base
    return "../" * num_steps_up  # For use in cMake, so always a forward slash here

