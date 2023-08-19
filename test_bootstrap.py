#!/bin/python3
import compile_all
# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import tempfile
import unittest

import bootstrap

""" Developer tests for the bootstrap module. """


class TestBootstrap(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.original_dir = os.getcwd()
        self.config = {"FreeCAD-version": "0.22",
                       "LibPack-version": "3.0.0",
                       "content": [
                           {"name": "nonexistent"}
                       ]}

    def tearDown(self) -> None:
        super().tearDown()

    def test_create_libpack_dir_no_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                dirname = bootstrap.create_libpack_dir(self.config, compile_all.BuildMode.RELEASE)
                self.assertNotEqual(dirname.find("0.22"), -1)
                self.assertNotEqual(dirname.find("3.0.0"), -1)
                self.assertNotEqual(dirname.find("Release"), -1)
            except:
                os.chdir(self.original_dir)  # Otherwise we can't unlink the directory on Windows
                raise
            os.chdir(self.original_dir)  # Otherwise we can't unlink the directory on Windows

    def test_create_libpack_dir_needs_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                first = bootstrap.create_libpack_dir(self.config, compile_all.BuildMode.RELEASE)
                second = bootstrap.create_libpack_dir(self.config, compile_all.BuildMode.RELEASE)
                self.assertTrue(os.path.exists(second))
                self.assertEqual(len(os.listdir()), 2)
            except:
                os.chdir(self.original_dir)  # Otherwise we can't unlink the directory on Windows
                raise
            os.chdir(self.original_dir)  # Otherwise we can't unlink the directory on Windows

    def test_too_many_backups_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Arrange
                os.chdir(temp_dir)
                config = {"FreeCAD-version": "0.22", "LibPack-version": "3.0.0"}
                for i in range(0, 27):
                    bootstrap.create_libpack_dir(self.config, compile_all.BuildMode.RELEASE)
                    self.assertEqual(len(os.listdir()), i + 1)
                # Act
                with self.assertRaises(SystemExit):
                    bootstrap.create_libpack_dir(self.config, compile_all.BuildMode.RELEASE)
            except:
                os.chdir(self.original_dir)  # Otherwise we can't unlink the directory on Windows
                raise
            os.chdir(self.original_dir)  # Otherwise we can't unlink the directory on Windows


if __name__ == "__main__":
    unittest.main()
