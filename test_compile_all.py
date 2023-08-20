#!/bin/python3

# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import unittest
from unittest.mock import MagicMock, patch, mock_open

import compile_all

""" Developer tests for the compile_all module. """


class TestCompileAll(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        config = {"FreeCAD-version": "0.22",
                  "LibPack-version": "3.0.0",
                  "content": [
                      {"name": "nonexistent"}
                  ]}
        self.compiler = compile_all.Compiler(config, compile_all.BuildMode.RELEASE, "bison_path")
        self.original_dir = os.getcwd()

    def tearDown(self) -> None:
        os.chdir(self.original_dir)
        super().tearDown()

    @patch("os.chdir")
    @patch("compile_all.Compiler.build_nonexistent")
    def test_compile_all_calls_build_function(self, nonexistent_mock: MagicMock, _):
        config = {"content": [
            {"name": "nonexistent"}
        ]}
        self.compiler.compile_all()
        nonexistent_mock.assert_called_once()

    @patch("subprocess.run")
    def test_get_python_version(self, run_mock: MagicMock):
        """ Checking the Python version stores the Major and Minor components (but not the Patch) """

        # Arrange
        mock_result = MagicMock()
        mock_result.stdout = b"Python 3.9.13"
        run_mock.return_value = mock_result

        # Act
        version = self.compiler.get_python_version()

        # Assert
        self.assertEqual(version, "3.9")

    def test_split_patch_data_finds_single_file(self):
        filename = "filename"
        patch_data = "@@ -1,3 +1,1 @@ -The +A"
        data = f"@@@ {filename} @@@\n{patch_data}"
        patches = compile_all.split_patch_data(data)
        self.assertEqual(1, len(patches))
        self.assertEqual(patches[0]["file"], filename)

    def test_split_patch_data_finds_multiple_files(self):
        filename = "filename"
        patch_data = "@@ -1,3 +1,1 @@\n-The +A"
        expected_number = 5
        data = ""
        for i in range(expected_number):
            data += f"@@@ {filename}{i} @@@\n{patch_data}"
        patches = compile_all.split_patch_data(data)
        self.assertEqual(expected_number, len(patches))


class TestPatchSingleFile(unittest.TestCase):

    @patch("builtins.open", mock_open(read_data="The End."))
    def test_integration_patch_single_file(self):
        mo = mock_open(read_data="The End.")
        with patch("builtins.open", mo):
            compile_all.patch_single_file("filename", "@@ -1,7 +1,5 @@\n-The\n+A\n  End\n")
        mo.assert_any_call("filename", "w", encoding="utf-8")
        handle = mo()
        handle.write.assert_called_once_with("A End.")


if __name__ == "__main__":
    unittest.main()
