#!/bin/python3
import os

# SPDX-License-Identifier: LGPL-2.1-or-later

import unittest
from unittest.mock import MagicMock, patch, mock_open

import path_cleaner


class TestPathCleaner(unittest.TestCase):
    """Tests the methods in path_cleaner.py"""

    def test_create_depth_string_simple(self):
        """The Depth String method should return a cMake-style string consisting of dots and slashes (never
        backslashes)."""
        # Arrange
        starts_with = os.path.join("some", "fake", "path")
        fake_file_path = os.path.join(starts_with, "to", "a", "file.txt")

        # Act
        result = path_cleaner.create_depth_string(starts_with, fake_file_path)

        # Assert
        self.assertEqual(
            "../../", result, "Expected a cMake-style path string going up two directories"
        )

    def test_create_depth_string_trailing_path_sep(self):
        """Even if there is an extraneous trailing path separator on the base path, the method should return the
        correct results."""
        # Arrange
        starts_with = os.path.join("some", "fake", "path")
        fake_file_path = os.path.join(starts_with, "to", "a", "file.txt")

        # Act
        result = path_cleaner.create_depth_string(starts_with + os.path.sep, fake_file_path)

        # Assert
        self.assertEqual(
            "../../", result, "Expected a cMake-style path string going up two directories"
        )

    def test_create_depth_string_extraneous_slashes(self):
        """Even if there are extraneous slashes in the path, it should still return the correct result"""
        # Arrange
        starts_with = os.path.join("some", "fake", "path")
        fake_file_path = os.path.join(starts_with, "to", "a", "file.txt")
        fake_file_path = fake_file_path.replace(os.path.sep, os.path.sep + os.path.sep)

        # Act
        result = path_cleaner.create_depth_string(starts_with, fake_file_path)

        # Assert
        self.assertEqual(
            "../../", result, "Expected a cMake-style path string going up two directories"
        )

    def test_remove_local_path_from_cmake_file(self):
        """Given a cMake file that contains some local paths, this should remove those local paths and convert them
        into references relative to the file's location."""
        # Arrange
        fake_cmake_data = (
            '    set(_BOOST_CMAKEDIR "Z:/FreeCAD/FreeCAD-LibPack-1.0.0-v3.0.0-Release/lib/cmake")\n'
        )
        cleaned_data = '    set(_BOOST_CMAKEDIR "${CMAKE_CURRENT_SOURCE_DIR}/../../lib/cmake")\n'

        # Act
        with patch("builtins.open", mock_open(read_data=fake_cmake_data)) as open_mock:
            path_cleaner.remove_local_path_from_cmake_file(
                "Z:\\FreeCAD\\FreeCAD-LibPack-1.0.0-v3.0.0-Release\\",
                "Z:\\FreeCAD\\FreeCAD-LibPack-1.0.0-v3.0.0-Release\\lib\\cmake\\mock.cmake",
            )

            # Assert (still in the context manager, so we can query the mocked file)
            open_mock().write.assert_called_with(cleaned_data)

    def test_remove_local_path_from_cmake_file_bad_path(self):
        """There is at least one package (MEDfile) that puts in a Windows-style path into cMake, even though they
        should not do so. Make sure we handle that."""
        # Arrange
        fake_cmake_data = (
            'SET(_hdf5_path "Z:\\FreeCAD\\FreeCAD-LibPack-1.0.0-v3.0.0-Release/share/cmake/")\n'
        )
        cleaned_data = 'SET(_hdf5_path "${CMAKE_CURRENT_SOURCE_DIR}/../../share/cmake/")\n'

        # Act
        with patch("builtins.open", mock_open(read_data=fake_cmake_data)) as open_mock:
            path_cleaner.remove_local_path_from_cmake_file(
                "Z:\\FreeCAD\\FreeCAD-LibPack-1.0.0-v3.0.0-Release\\",
                "Z:\\FreeCAD\\FreeCAD-LibPack-1.0.0-v3.0.0-Release\\share\\cmake\\mock.cmake",
            )

            # Assert (still in the context manager, so we can query the mocked file)
            open_mock().write.assert_called_with(cleaned_data)


if __name__ == "__main__":
    unittest.main()
