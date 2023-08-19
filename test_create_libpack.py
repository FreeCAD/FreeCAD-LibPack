#!/bin/python3

# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import shutil
from subprocess import CalledProcessError
import tempfile
import unittest
from unittest.mock import MagicMock, patch, mock_open

import create_libpack


class TestDeleteExisting(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        super().tearDown()
        shutil.rmtree(self.temp_dir.name)

    @patch("builtins.print")
    def test_no_directory_not_silent(self, mock_print: MagicMock):
        """Nothing happens when asking to delete a directory that does not exist"""
        create_libpack.delete_existing(
            os.path.join(self.temp_dir.name, "no_such_dir"), silent=False
        )
        mock_print.assert_not_called()

    @patch("builtins.print")
    def test_with_directory_silent_is_silent(self, mock_print: MagicMock):
        """In silent mode, nothing is printed even when deleting"""
        dir_to_delete = os.path.join(self.temp_dir.name, "existing_dir")
        os.mkdir(dir_to_delete)
        create_libpack.delete_existing(dir_to_delete, silent=True)
        mock_print.assert_not_called()

    @patch("builtins.print")
    def test_with_directory_silent_deletes_dir(self, mock_print: MagicMock):
        """In silent mode, the directory is deleted"""
        dir_to_delete = os.path.join(self.temp_dir.name, "existing_dir")
        os.mkdir(dir_to_delete)
        create_libpack.delete_existing(dir_to_delete, silent=True)
        self.assertFalse(os.path.exists(dir_to_delete))

    @patch("builtins.input")
    def test_with_directory_not_silent_asks_for_confirmation(
        self, mock_input: MagicMock
    ):
        """When not in silent mode, the user is asked to confirm"""
        dir_to_delete = os.path.join(self.temp_dir.name, "existing_dir")
        os.mkdir(dir_to_delete)
        create_libpack.delete_existing(dir_to_delete, silent=False)
        mock_input.assert_called_once()

    @patch("builtins.input")
    def test_confirm_defaults_to_no(self, mock_input: MagicMock):
        """If the user just hits enter, the default is to NOT delete the directory"""
        dir_to_delete = os.path.join(self.temp_dir.name, "existing_dir")
        mock_input.return_value = ""
        os.mkdir(dir_to_delete)
        create_libpack.delete_existing(dir_to_delete, silent=False)
        self.assertTrue(os.path.exists(dir_to_delete))

    @patch("builtins.input")
    def test_confirm_with_y_deletes(self, mock_input: MagicMock):
        """If the user types 'y' then the directory is deleted"""
        dir_to_delete = os.path.join(self.temp_dir.name, "existing_dir")
        mock_input.return_value = "y"
        os.mkdir(dir_to_delete)
        create_libpack.delete_existing(dir_to_delete, silent=False)
        self.assertFalse(os.path.exists(dir_to_delete))


class TestLoadConfig(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        super().tearDown()
        shutil.rmtree(self.temp_dir.name)

    @patch("builtins.open", mock_open(read_data='{"entry1":1,"entry2":2}'))
    def test_json_is_loaded(self):
        """When appropriate JSON data exists it is loaded and returned"""
        loaded_data = create_libpack.load_config(self.temp_dir.name)
        self.assertIn("entry1", loaded_data)
        self.assertIn("entry2", loaded_data)

    @patch("builtins.print")
    def test_non_existent_file_prints_error(self, mock_print: MagicMock):
        """If a non-existent file is given, an error is printed (and exit() is called)"""
        with self.assertRaises(SystemExit):
            create_libpack.load_config(
                os.path.join(self.temp_dir.name, "no_such_file.json")
            )
        mock_print.assert_called_once()

    @patch("builtins.print")
    @patch("builtins.open", mock_open(read_data="bad json data!"))
    def test_bad_file_prints_error(self, mock_print: MagicMock):
        """If a bad JSON data is given, an error is printed (and exit() is called)"""
        with self.assertRaises(SystemExit):
            create_libpack.load_config(self.temp_dir.name)
        mock_print.assert_called_once()


class TestRemoteFetchFunctions(unittest.TestCase):
    """Git and direct download"""

    def setUp(self) -> None:
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        super().tearDown()
        shutil.rmtree(self.temp_dir.name)

    @patch("create_libpack.clone")
    def test_repos_are_discovered(self, mock_clone: MagicMock):
        """Any dictionary with both a git-repo and a git-ref is passed along to clone"""
        test_config = {
            "content": [
                {"name": "test1", "git-repo": "test1_repo", "git-ref": "test1_ref"},
                {"name": "test2", "git-repo": "test2_repo", "git-ref": "test2_ref"},
                {"name": "test3", "git-repo": "test3_repo", "git-ref": "test3_ref"},
            ]
        }
        create_libpack.fetch_remote_data(test_config)
        self.assertEqual(mock_clone.call_count, 3)

    @patch("builtins.print")
    def test_missing_repo_errors_if_ref(self, mock_print: MagicMock):
        """An entry with a git-ref but no git-repo is an error"""
        test_config = {"content": [{"name": "test1", "git-ref": "test1_ref"}]}
        with self.assertRaises(SystemExit):
            create_libpack.fetch_remote_data(test_config)
        mock_print.assert_called()

    @patch("create_libpack.clone")
    def test_missing_ref_is_omitted(self, mock_clone: MagicMock):
        """An entry with a git-repo but no git-ref just doesn't use the ref"""
        test_config = {
            "content": [
                {"name": "test1", "git-repo": "test1_repo"},
            ]
        }
        create_libpack.fetch_remote_data(test_config)
        mock_clone.assert_called_once_with("test1", "test1_repo")

    @patch("create_libpack.clone")
    def test_non_git_entries_are_ignored(self, mock_clone: MagicMock):
        """Non-git entries are just ignored"""
        test_config = {
            "content": [
                {"name": "test1"},
            ]
        }
        create_libpack.fetch_remote_data(test_config)
        mock_clone.assert_not_called()

    @patch("subprocess.run")
    def test_clone_calls_git_with_ref(self, run_mock: MagicMock):
        """When given a repo and a ref, git clone is set up appropriately"""
        create_libpack.clone("name", "https://some.url", "some_git_ref")
        run_mock.assert_called_once()
        call_data: list = run_mock.call_args[0][0]
        self.assertIn("https://some.url", call_data)
        self.assertIn("some_git_ref", call_data)
        self.assertEquals(call_data[-1], "name")

    @patch("subprocess.run")
    def test_clone_calls_git_without_ref(self, run_mock: MagicMock):
        """When given a repo and a ref, git clone is set up appropriately"""
        create_libpack.clone("test", "https://some.url")
        run_mock.assert_called_once()
        call_data = run_mock.call_args[0][0]
        self.assertNotIn(None, call_data)
        self.assertNotIn("--branch", call_data)

    @patch("subprocess.run")
    def test_exception_is_caught_and_calls_exit(self, run_mock: MagicMock):
        """When given a repo and a ref, git clone is set up appropriately"""
        run_mock.side_effect = CalledProcessError(1, "command_that_was_called")
        with self.assertRaises(SystemExit):
            create_libpack.clone("some_name", "https://some.url")

    @patch("os.path.exists", MagicMock(return_value=True))
    @patch("create_libpack.clone")
    def test_skips_existing_paths_with_flag(self, clone_mock: MagicMock):
        test_config = {
            "content": [
                {"name": "test1", "git-repo": "test1_repo", "git-ref": "test1_ref"},
                {"name": "test2", "git-repo": "test2_repo", "git-ref": "test2_ref"},
                {"name": "test3", "git-repo": "test3_repo", "git-ref": "test3_ref"},
            ]
        }
        create_libpack.fetch_remote_data(test_config, skip_existing=True)
        clone_mock.assert_not_called()

    @patch("create_libpack.download")
    def test_url_calls_download(self, download_mock: MagicMock):
        test_config = {"content": [{"name": "test", "url": "https://some.url"}]}
        create_libpack.fetch_remote_data(test_config)
        download_mock.assert_called_once()

    @patch("os.mkdir")  # Patch so it doesn't actually make a directory
    @patch("requests.get")  # Patch so no network request is made
    @patch("create_libpack.decompress")  # Patch so no attempt is made to decompress
    def test_download_creates_file(self, decompress_mock: MagicMock, _1, _2):
        with patch("builtins.open", mock_open()) as open_mock:
            create_libpack.download("make_this_dir", "https://some.url/test.7z")
            open_mock.assert_called_once_with(
                os.path.join("make_this_dir", "test.7z"), "wb"
            )
        decompress_mock.assert_called_once_with("make_this_dir", "test.7z")

    @patch("os.chdir")
    @patch("subprocess.run")
    def test_decompress_calls_subprocess(
        self, run_mock: MagicMock, chdir_mock: MagicMock
    ):
        create_libpack.decompress("path_to_file", "file_name")
        run_mock.assert_called_once()
        self.assertEqual(chdir_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
