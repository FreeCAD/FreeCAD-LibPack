#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

import os
import unittest
from unittest.mock import MagicMock, patch, mock_open

import compile_all

""" Developer tests for the compile_all module. """


class TestCompileAll(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        config = {
            "FreeCAD-version": "0.22",
            "LibPack-version": "3.0.0",
            "content": [{"name": "nonexistent"}],
        }
        self.compiler = compile_all.Compiler(config, compile_all.BuildMode.RELEASE, "bison_path")
        self.original_dir = os.getcwd()

    def tearDown(self) -> None:
        os.chdir(self.original_dir)
        super().tearDown()

    def test_default_generator_args_uses_configured_generator(self):
        """Without an explicit generator, the configured one is passed to CMake."""
        self.compiler.cmake_generator = "Visual Studio 17 2022"
        self.assertEqual(
            self.compiler._default_generator_args(["-D FOO=BAR"]),
            ["-G", "Visual Studio 17 2022"],
        )
        self.assertEqual(
            self.compiler._default_generator_args(None),
            ["-G", "Visual Studio 17 2022"],
        )

    def test_default_generator_args_respects_explicit_generator(self):
        """A package that selects its own generator, such as Ninja, keeps that choice."""
        self.compiler.cmake_generator = "Visual Studio 17 2022"
        self.assertEqual(self.compiler._default_generator_args(["-G", "Ninja"]), [])
        self.assertEqual(self.compiler._default_generator_args(["-GNinja"]), [])

    def test_default_generator_args_without_configured_generator(self):
        """With no configured generator the choice is left to CMake."""
        self.assertEqual(self.compiler._default_generator_args(["-D FOO=BAR"]), [])

    @patch("compile_all.Compiler._run_cmake")
    @patch("compile_all.Compiler.get_cmake_options", return_value=[])
    def test_cmake_configure_passes_configured_generator(self, _, run_cmake_mock: MagicMock):
        """Configuring a package passes the configured generator through to CMake."""
        self.compiler.cmake_generator = "Visual Studio 17 2022"
        self.compiler._cmake_configure(["-D FOO=BAR"])
        options = run_cmake_mock.call_args[0][0]
        self.assertEqual(options[options.index("-G") + 1], "Visual Studio 17 2022")

    def test_toolset_args_apply_to_the_configured_visual_studio_generator(self):
        """Without an explicit generator, the configured toolset accompanies the VS generator."""
        self.compiler.cmake_generator = "Visual Studio 18 2026"
        self.compiler.cmake_toolset = "v143,version=14.44.17.14"
        self.assertEqual(
            self.compiler._toolset_args(["-D FOO=BAR"]), ["-T", "v143,version=14.44.17.14"]
        )

    def test_toolset_args_are_omitted_for_ninja(self):
        """Ninja rejects -T, and already uses the compiler selected by vcvars."""
        self.compiler.cmake_generator = "Visual Studio 18 2026"
        self.compiler.cmake_toolset = "v143,version=14.44.17.14"
        self.assertEqual(self.compiler._toolset_args(["-G", "Ninja"]), [])
        self.assertEqual(self.compiler._toolset_args(["-GNinja"]), [])

    def test_toolset_args_without_configured_toolset(self):
        """With no configured toolset the choice is left to CMake."""
        self.compiler.cmake_generator = "Visual Studio 18 2026"
        self.assertEqual(self.compiler._toolset_args(None), [])

    @patch("compile_all.Compiler._run_cmake")
    @patch("compile_all.Compiler.get_cmake_options", return_value=[])
    def test_cmake_configure_passes_configured_toolset(self, _, run_cmake_mock: MagicMock):
        """Configuring a package passes the configured toolset through to CMake."""
        self.compiler.cmake_generator = "Visual Studio 18 2026"
        self.compiler.cmake_toolset = "v143,version=14.44.17.14"
        self.compiler._cmake_configure(["-D FOO=BAR"])
        options = run_cmake_mock.call_args[0][0]
        self.assertEqual(options[options.index("-T") + 1], "v143,version=14.44.17.14")

    def test_msbuild_toolset_args(self):
        """A direct msbuild invocation is pinned to the requested tools."""
        self.compiler.msvc_platform_toolset = "v143"
        self.compiler.msvc_tools_version = "14.44.35207"
        self.assertEqual(
            self.compiler._msbuild_toolset_args(),
            ["/p:PlatformToolset=v143", "/p:VCToolsVersion=14.44.35207"],
        )

    def test_msbuild_toolset_args_without_requested_tools(self):
        """Without a requested version msbuild keeps its default."""
        self.assertEqual(self.compiler._msbuild_toolset_args(), [])

    @patch("sys.platform", "win32")
    @patch("compile_all.Compiler._run_streaming")
    @patch("compile_all.Compiler.python_exe", return_value="python")
    def test_release_pip_install_uses_the_requested_compiler(self, _, run_mock: MagicMock):
        """Release sdist builds run under vcvars and trust that environment."""
        self.compiler.mode = compile_all.BuildMode.RELEASE
        self.compiler.init_script = ["vcvars.bat", "-vcvars_ver=14.4"]
        self.compiler._run_pip_install(["pkg==1.0"], False, ())
        args = run_mock.call_args[0][0]
        self.assertEqual(args[:3], ["vcvars.bat", "-vcvars_ver=14.4", "&"])
        self.assertEqual(run_mock.call_args[1]["env"]["DISTUTILS_USE_SDK"], "1")

    @patch("os.chdir")
    @patch("compile_all.Compiler.build_nonexistent")
    def test_compile_all_calls_build_function(self, nonexistent_mock: MagicMock, _):
        config = {"content": [{"name": "nonexistent"}]}
        self.compiler.compile_all()
        nonexistent_mock.assert_called_once()

    @patch("subprocess.run")
    def test_get_python_version(self, run_mock: MagicMock):
        """Checking the Python version stores the Major and Minor components (but not the Patch)"""

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
