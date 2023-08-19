#!/bin/python3

# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import unittest
from unittest.mock import MagicMock, patch

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
    def test_check_python_version(self, run_mock: MagicMock):
        """ Checking the Python version stores the Major and Minor components (but not the Patch) """

        # Arrange
        mock_result = MagicMock()
        mock_result.stdout = b"Python 3.9.13"
        run_mock.return_value = mock_result

        # Act
        version = self.compiler.get_python_version()

        # Assert
        self.assertEqual(version, "3.9")


if __name__ == "__main__":
    unittest.main()
