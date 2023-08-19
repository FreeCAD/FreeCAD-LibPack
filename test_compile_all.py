#!/bin/python3

# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import tempfile
import unittest
from unittest.mock import MagicMock, mock_open, patch

import compile_all


class TestCompileAll(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        config = {"FreeCAD-version": "0.22", 
                  "LibPack-version": "3.0.0",
                  "content":[
                  {"name":"nonexistent"}
        ]}
        self.compiler = compile_all.Compiler(config, compile_all.build_mode.RELEASE, "bison_path")
        self.original_dir = os.getcwd()

    def tearDown(self) -> None:
        os.chdir(self.original_dir)
        super().tearDown()

    @patch("compile_all.Compiler.create_libpack_dir")
    @patch("os.chdir")
    @patch("compile_all.Compiler.build_nonexistent")
    def test_compile_all_calls_build_function(self, nonexistent_mock:MagicMock, _1, _2):
        config = {"content":[
            {"name":"nonexistent"}
        ]}
        self.compiler.compile_all()
        nonexistent_mock.assert_called_once()

    def test_create_libpack_dir_no_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            dirname = self.compiler.create_libpack_dir()
            self.assertNotEqual(dirname.find("0.22"), -1)
            self.assertNotEqual(dirname.find("3.0.0"), -1)
            self.assertNotEqual(dirname.find("release"), -1)
            os.chdir(self.original_dir) # Otherwise we can't unlink the directory on Windows

    def test_create_libpack_dir_needs_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            first = self.compiler.create_libpack_dir()
            second = self.compiler.create_libpack_dir()
            self.assertTrue(os.path.exists(second))
            self.assertEqual(len(os.listdir()), 2)
            os.chdir(self.original_dir) # Otherwise we can't unlink the directory on Windows

    def test_too_many_backups_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Arrange
                os.chdir(temp_dir)
                config = {"FreeCAD-version": "0.22", "LibPack-version": "3.0.0"}
                for i in range(0,27):
                        self.compiler.create_libpack_dir()
                        self.assertEqual(len(os.listdir()), i+1)
                # Act
                with self.assertRaises(SystemExit):
                    self.compiler.create_libpack_dir()
            except:
                os.chdir(self.original_dir) # Otherwise we can't unlink the directory on Windows
                raise
            os.chdir(self.original_dir) # Otherwise we can't unlink the directory on Windows



if __name__ == "__main__":
    unittest.main()
