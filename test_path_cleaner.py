#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

import os
import shutil
import tempfile

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


class TestDeleteExtraneousFiles(unittest.TestCase):
    """Verifies that delete_extraneous_files removes both files and directories listed in paths_to_delete."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="libpack_test_")

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def test_removes_directory_entry(self):
        """The samples entry in paths_to_delete is a directory, so the cleaner must use a recursive removal rather
        than os.unlink."""
        # Arrange
        samples_dir = os.path.join(self.base_dir, "samples")
        nested = os.path.join(samples_dir, "tcl")
        os.makedirs(nested)
        with open(os.path.join(nested, "demo.tcl"), "w", encoding="utf-8") as f:
            f.write("# sample\n")

        # Act
        path_cleaner.delete_extraneous_files(self.base_dir)

        # Assert
        self.assertFalse(
            os.path.exists(samples_dir),
            "Expected the samples directory to be removed by delete_extraneous_files",
        )

    def test_removes_file_entry(self):
        """A plain file listed in paths_to_delete should still be removed."""
        # Arrange
        target = os.path.join(self.base_dir, "env.bat")
        with open(target, "w", encoding="utf-8") as f:
            f.write("REM\n")

        # Act
        path_cleaner.delete_extraneous_files(self.base_dir)

        # Assert
        self.assertFalse(os.path.exists(target))

    def test_missing_entry_is_not_an_error(self):
        """If none of the listed entries exist, the function should still return cleanly."""
        # Act / Assert
        path_cleaner.delete_extraneous_files(self.base_dir)


class TestDeleteUnusedStaticLibs(unittest.TestCase):
    """Verifies that the LLVM, Clang, LLD, and clazy static libraries are removed and that nothing else is touched."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="libpack_test_")
        self.lib_dir = os.path.join(self.base_dir, "lib")
        os.makedirs(self.lib_dir)

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def _touch(self, name: str) -> str:
        path = os.path.join(self.lib_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        return path

    def test_removes_target_static_libs(self):
        """All clang*.lib, LLVM*.lib, lld*.lib, and clazy*.lib files should be removed."""
        # Arrange
        targets = [
            self._touch("clangAST.lib"),
            self._touch("clangSema.lib"),
            self._touch("LLVMCore.lib"),
            self._touch("LLVMSupport.lib"),
            self._touch("clazyPlugin.lib"),
            self._touch("lldCommon.lib"),
        ]

        # Act
        removed = path_cleaner.delete_unused_static_libs(self.base_dir)

        # Assert
        self.assertEqual(removed, len(targets))
        for path in targets:
            self.assertFalse(os.path.exists(path), f"Expected {path} to be removed")

    def test_preserves_other_libs(self):
        """Unrelated libraries and non-.lib files must not be touched."""
        # Arrange
        keepers = [
            self._touch("Qt6Core.lib"),
            self._touch("boost_filesystem.lib"),
            self._touch("python314.lib"),
            self._touch("clang-readme.txt"),
        ]

        # Act
        removed = path_cleaner.delete_unused_static_libs(self.base_dir)

        # Assert
        self.assertEqual(removed, 0)
        for path in keepers:
            self.assertTrue(os.path.exists(path), f"Expected {path} to be preserved")

    def test_no_lib_dir_is_not_an_error(self):
        """If the LibPack has no lib/ directory the function should be a no-op."""
        # Arrange
        shutil.rmtree(self.lib_dir)

        # Act / Assert
        self.assertEqual(path_cleaner.delete_unused_static_libs(self.base_dir), 0)


class TestDeleteDocumentation(unittest.TestCase):
    """Verifies that share/doc and the top-level Qt qdoc input directory are both removed."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="libpack_test_")

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def _make_dir_with_file(self, *parts: str) -> str:
        path = os.path.join(self.base_dir, *parts)
        os.makedirs(path)
        with open(os.path.join(path, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html></html>")
        return path

    def test_removes_share_doc_tree(self):
        """Every package's documentation under share/doc should be removed in one shot."""
        # Arrange
        med = self._make_dir_with_file("share", "doc", "med-fichier-6.0.1")
        gmsh = self._make_dir_with_file("share", "doc", "gmsh")
        zlib = self._make_dir_with_file("share", "doc", "zlib")

        # Act
        path_cleaner.delete_documentation(self.base_dir)

        # Assert
        for path in (med, gmsh, zlib):
            self.assertFalse(os.path.exists(path), f"Expected {path} to be removed")
        self.assertFalse(os.path.exists(os.path.join(self.base_dir, "share", "doc")))

    def test_removes_top_level_qt_doc_tree(self):
        """The top-level doc/ directory containing Qt's qdoc input must be removed."""
        # Arrange
        config = self._make_dir_with_file("doc", "config")
        global_dir = self._make_dir_with_file("doc", "global")

        # Act
        path_cleaner.delete_documentation(self.base_dir)

        # Assert
        for path in (config, global_dir):
            self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(os.path.join(self.base_dir, "doc")))

    def test_returns_count_of_removed_top_level_trees(self):
        """The return value should reflect how many of the documentation roots were actually present."""
        # Arrange
        self._make_dir_with_file("share", "doc", "gmsh")
        self._make_dir_with_file("doc", "config")

        # Act
        removed = path_cleaner.delete_documentation(self.base_dir)

        # Assert
        self.assertEqual(removed, 2)

    def test_preserves_unrelated_share_directories(self):
        """share/cmake and other share/ siblings must not be touched."""
        # Arrange
        share_cmake = self._make_dir_with_file("share", "cmake", "freetype")
        self._make_dir_with_file("share", "doc", "gmsh")

        # Act
        path_cleaner.delete_documentation(self.base_dir)

        # Assert
        self.assertTrue(os.path.exists(share_cmake))

    def test_missing_doc_trees_is_not_an_error(self):
        """If neither documentation root exists the function should be a no-op."""
        # Act / Assert
        self.assertEqual(path_cleaner.delete_documentation(self.base_dir), 0)


class TestDeleteOcctSampleData(unittest.TestCase):
    """Verifies that the top-level data/ directory of OCCT sample geometry is removed."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="libpack_test_")

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def test_removes_data_directory(self):
        """The data/ tree and every subdirectory under it should be removed."""
        # Arrange
        for sub in ("stl", "occ", "iges", "step", "images", "vrml"):
            os.makedirs(os.path.join(self.base_dir, "data", sub))
        with open(os.path.join(self.base_dir, "data", "stl", "bearing.stl"), "w", encoding="utf-8") as f:
            f.write("solid")

        # Act
        result = path_cleaner.delete_occt_sample_data(self.base_dir)

        # Assert
        self.assertTrue(result)
        self.assertFalse(os.path.exists(os.path.join(self.base_dir, "data")))

    def test_preserves_unrelated_top_level_dirs(self):
        """Other top-level directories must not be touched."""
        # Arrange
        bin_dir = os.path.join(self.base_dir, "bin")
        os.makedirs(bin_dir)
        os.makedirs(os.path.join(self.base_dir, "data"))

        # Act
        path_cleaner.delete_occt_sample_data(self.base_dir)

        # Assert
        self.assertTrue(os.path.exists(bin_dir))

    def test_missing_data_directory_is_not_an_error(self):
        """If data/ does not exist the function should return False without raising."""
        # Act / Assert
        self.assertFalse(path_cleaner.delete_occt_sample_data(self.base_dir))


class TestDeleteLldb(unittest.TestCase):
    """Verifies that the LLDB runtime DLLs and Python bindings are removed."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="libpack_test_")

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def _touch(self, *parts: str) -> str:
        path = os.path.join(self.base_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        return path

    def _make_dir(self, *parts: str) -> str:
        path = os.path.join(self.base_dir, *parts)
        os.makedirs(path)
        with open(os.path.join(path, "__init__.py"), "w", encoding="utf-8") as f:
            f.write("")
        return path

    def test_removes_lldb_dlls_and_bindings(self):
        """liblldb.dll, liblldb-original.dll, and the lldb Python package directories should all be removed."""
        # Arrange
        targets = [
            self._touch("bin", "liblldb.dll"),
            self._touch("bin", "liblldb-original.dll"),
            self._make_dir("lib", "site-packages", "lldb"),
            self._make_dir("bin", "Lib", "site-packages", "lldb"),
        ]

        # Act
        removed = path_cleaner.delete_lldb(self.base_dir)

        # Assert
        self.assertEqual(removed, len(targets))
        for path in targets:
            self.assertFalse(os.path.exists(path), f"Expected {path} to be removed")

    def test_preserves_libclang_dll(self):
        """libclang.dll lives in the same bin/ directory and must not be touched."""
        # Arrange
        keeper = self._touch("bin", "libclang.dll")

        # Act
        path_cleaner.delete_lldb(self.base_dir)

        # Assert
        self.assertTrue(os.path.exists(keeper))

    def test_missing_runtime_is_not_an_error(self):
        """If none of the LLDB paths exist the function should return zero."""
        # Act / Assert
        self.assertEqual(path_cleaner.delete_lldb(self.base_dir), 0)


class TestDeleteBundledCmake(unittest.TestCase):
    """Verifies that the cmake pip package is removed without disturbing other site-packages content."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="libpack_test_")
        self.site_packages = os.path.join(self.base_dir, "bin", "Lib", "site-packages")
        os.makedirs(self.site_packages)

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def test_removes_cmake_package(self):
        """The cmake pip package must be removed entirely."""
        # Arrange
        cmake_pkg = os.path.join(self.site_packages, "cmake", "data", "bin")
        os.makedirs(cmake_pkg)
        with open(os.path.join(cmake_pkg, "cmake.exe"), "w", encoding="utf-8") as f:
            f.write("MZ")

        # Act
        result = path_cleaner.delete_bundled_cmake(self.base_dir)

        # Assert
        self.assertTrue(result)
        self.assertFalse(os.path.exists(os.path.join(self.site_packages, "cmake")))

    def test_preserves_other_packages(self):
        """Other site-packages entries must not be touched."""
        # Arrange
        os.makedirs(os.path.join(self.site_packages, "cmake"))
        numpy_dir = os.path.join(self.site_packages, "numpy")
        os.makedirs(numpy_dir)

        # Act
        path_cleaner.delete_bundled_cmake(self.base_dir)

        # Assert
        self.assertTrue(os.path.exists(numpy_dir))

    def test_missing_package_is_not_an_error(self):
        """If the cmake package is not installed the function should return False."""
        # Act / Assert
        self.assertFalse(path_cleaner.delete_bundled_cmake(self.base_dir))


class TestDeleteLlvmInternalHeaders(unittest.TestCase):
    """Verifies that the LLVM/Clang/LLDB internal headers are removed while the C ABI directories survive."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="libpack_test_")
        self.include_dir = os.path.join(self.base_dir, "include")
        os.makedirs(self.include_dir)

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def _make_header_dir(self, name: str, header: str = "header.h") -> str:
        path = os.path.join(self.include_dir, name)
        os.makedirs(path)
        with open(os.path.join(path, header), "w", encoding="utf-8") as f:
            f.write("// header\n")
        return path

    def test_removes_internal_header_dirs(self):
        """include/clang, include/clang-tidy, include/llvm, and include/lldb should be removed."""
        # Arrange
        targets = [
            self._make_header_dir("clang"),
            self._make_header_dir("clang-tidy"),
            self._make_header_dir("llvm"),
            self._make_header_dir("lldb"),
        ]

        # Act
        removed = path_cleaner.delete_llvm_internal_headers(self.base_dir)

        # Assert
        self.assertEqual(removed, len(targets))
        for path in targets:
            self.assertFalse(os.path.exists(path), f"Expected {path} to be removed")

    def test_preserves_c_abi_header_dirs(self):
        """include/clang-c and include/llvm-c (the libclang and libLLVM C ABI headers FreeCAD relies on) must
        survive."""
        # Arrange
        clang_c = self._make_header_dir("clang-c", "Index.h")
        llvm_c = self._make_header_dir("llvm-c", "Core.h")
        self._make_header_dir("clang")
        self._make_header_dir("llvm")

        # Act
        path_cleaner.delete_llvm_internal_headers(self.base_dir)

        # Assert
        self.assertTrue(os.path.exists(os.path.join(clang_c, "Index.h")))
        self.assertTrue(os.path.exists(os.path.join(llvm_c, "Core.h")))

    def test_preserves_unrelated_header_dirs(self):
        """Other include subdirectories (boost, Qt, etc.) must not be touched."""
        # Arrange
        boost = self._make_header_dir("boost-1_91", "version.hpp")

        # Act
        path_cleaner.delete_llvm_internal_headers(self.base_dir)

        # Assert
        self.assertTrue(os.path.exists(boost))

    def test_missing_include_dir_is_not_an_error(self):
        """If include/ does not exist the function should be a no-op."""
        # Arrange
        shutil.rmtree(self.include_dir)

        # Act / Assert
        self.assertEqual(path_cleaner.delete_llvm_internal_headers(self.base_dir), 0)


class TestDeletePdbFiles(unittest.TestCase):
    """Verifies that every .pdb file is removed regardless of where it lives in the tree."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="libpack_test_")

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def _touch(self, *parts: str) -> str:
        path = os.path.join(self.base_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        return path

    def test_removes_pdb_files_anywhere(self):
        """PDB files in bin, lib, and nested site-packages locations should all be removed."""
        # Arrange
        pdbs = [
            self._touch("bin", "python314.pdb"),
            self._touch("bin", "DLLs", "sqlite3.pdb"),
            self._touch("lib", "icuin.pdb"),
            self._touch("bin", "Lib", "site-packages", "debugpy", "_vendored", "inject.pdb"),
        ]

        # Act
        removed = path_cleaner.delete_pdb_files(self.base_dir)

        # Assert
        self.assertEqual(removed, len(pdbs))
        for path in pdbs:
            self.assertFalse(os.path.exists(path), f"Expected {path} to be removed")

    def test_case_insensitive_extension(self):
        """A file with an upper-case .PDB extension should still be matched."""
        # Arrange
        target = self._touch("bin", "Foo.PDB")

        # Act
        removed = path_cleaner.delete_pdb_files(self.base_dir)

        # Assert
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(target))

    def test_preserves_non_pdb_files(self):
        """Files that merely contain pdb in their name must not be removed."""
        # Arrange
        keepers = [
            self._touch("bin", "python.exe"),
            self._touch("bin", "pdb.py"),
            self._touch("lib", "Qt6Core.lib"),
            self._touch("bin", "pdbcheck.txt"),
        ]

        # Act
        path_cleaner.delete_pdb_files(self.base_dir)

        # Assert
        for path in keepers:
            self.assertTrue(os.path.exists(path), f"Expected {path} to be preserved")

    def test_empty_tree_is_not_an_error(self):
        """An empty LibPack tree should produce a count of zero."""
        # Act / Assert
        self.assertEqual(path_cleaner.delete_pdb_files(self.base_dir), 0)


class TestDeletePythonTestSuites(unittest.TestCase):
    """Verifies removal of bundled Python test suites without disturbing other content."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="libpack_test_")
        self.site_packages = os.path.join(self.base_dir, "bin", "Lib", "site-packages")
        os.makedirs(self.site_packages)

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def _make_pkg_dir(self, *parts: str) -> str:
        path = os.path.join(self.site_packages, *parts)
        os.makedirs(path)
        with open(os.path.join(path, "__init__.py"), "w", encoding="utf-8") as f:
            f.write("")
        return path

    def test_removes_stdlib_test_directory(self):
        """bin/Lib/test should be removed."""
        # Arrange
        stdlib_test = os.path.join(self.base_dir, "bin", "Lib", "test")
        os.makedirs(stdlib_test)
        with open(os.path.join(stdlib_test, "test_grammar.py"), "w", encoding="utf-8") as f:
            f.write("")

        # Act
        path_cleaner.delete_python_test_suites(self.base_dir)

        # Assert
        self.assertFalse(os.path.exists(stdlib_test))

    def test_removes_site_packages_test_dirs(self):
        """Per-package test and tests directories under site-packages should be removed."""
        # Arrange
        scipy_tests = self._make_pkg_dir("scipy", "stats", "tests")
        numpy_tests = self._make_pkg_dir("numpy", "_core", "tests")
        nltk_test = self._make_pkg_dir("nltk", "test")
        nested_tests = self._make_pkg_dir("matplotlib", "tests", "baseline_images")

        # Act
        path_cleaner.delete_python_test_suites(self.base_dir)

        # Assert
        self.assertFalse(os.path.exists(scipy_tests))
        self.assertFalse(os.path.exists(numpy_tests))
        self.assertFalse(os.path.exists(nltk_test))
        self.assertFalse(os.path.exists(nested_tests))

    def test_preserves_non_test_packages(self):
        """Regular package code must not be affected."""
        # Arrange
        scipy_stats = self._make_pkg_dir("scipy", "stats")
        numpy_core = self._make_pkg_dir("numpy", "_core")

        # Act
        path_cleaner.delete_python_test_suites(self.base_dir)

        # Assert
        self.assertTrue(os.path.exists(os.path.join(scipy_stats, "__init__.py")))
        self.assertTrue(os.path.exists(os.path.join(numpy_core, "__init__.py")))

    def test_does_not_descend_into_removed_dirs(self):
        """If a tests directory contains a nested tests subdirectory, the parent removal must still succeed and the
        function must not attempt to recurse into the now-deleted parent."""
        # Arrange
        outer = self._make_pkg_dir("pkg", "tests")
        inner = os.path.join(outer, "tests")
        os.makedirs(inner)

        # Act
        removed = path_cleaner.delete_python_test_suites(self.base_dir)

        # Assert
        self.assertFalse(os.path.exists(outer))
        self.assertEqual(removed, 1)

    def test_missing_layout_is_not_an_error(self):
        """If neither the stdlib test directory nor site-packages exist the function should be a no-op."""
        # Arrange
        shutil.rmtree(self.site_packages)

        # Act / Assert
        self.assertEqual(path_cleaner.delete_python_test_suites(self.base_dir), 0)


if __name__ == "__main__":
    unittest.main()
