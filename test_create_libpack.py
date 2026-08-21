#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

import contextlib
import os
import shutil
from subprocess import CalledProcessError
import tempfile
import unittest
from unittest.mock import MagicMock, patch, mock_open

import requests

import create_libpack
from compile_all import BuildMode


@contextlib.contextmanager
def in_directory(path: str):
    original = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)


class FakeResponse:
    """Stands in for the streaming response returned by requests.get."""

    def __init__(self, payload: bytes, content_length: str = None):
        self.payload = payload
        length = str(len(payload)) if content_length is None else content_length
        self.headers = {"Content-Length": length}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size: int = 1):
        for start in range(0, len(self.payload), chunk_size):
            yield self.payload[start : start + chunk_size]


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
    def test_with_directory_not_silent_asks_for_confirmation(self, mock_input: MagicMock):
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
            create_libpack.load_config(os.path.join(self.temp_dir.name, "no_such_file.json"))
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
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE)
        self.assertEqual(mock_clone.call_count, 3)

    @patch("builtins.print")
    def test_missing_repo_errors_if_ref(self, mock_print: MagicMock):
        """An entry with a git-ref but no git-repo is an error"""
        test_config = {"content": [{"name": "test1", "git-ref": "test1_ref"}]}
        with self.assertRaises(SystemExit):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE)
        mock_print.assert_called()

    @patch("create_libpack.clone")
    def test_missing_ref_is_omitted(self, mock_clone: MagicMock):
        """An entry with a git-repo but no git-ref just doesn't use the ref"""
        test_config = {
            "content": [
                {"name": "test1", "git-repo": "test1_repo"},
            ]
        }
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE)
        mock_clone.assert_called_once_with("test1", "test1_repo", None, None)

    @patch("create_libpack.clone")
    def test_non_git_entries_are_ignored(self, mock_clone: MagicMock):
        """Non-git entries are just ignored"""
        test_config = {
            "content": [
                {"name": "test1"},
            ]
        }
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE)
        mock_clone.assert_not_called()

    @patch("create_libpack.clone")
    @patch("create_libpack.download")
    def test_hybrid_entry_clones_in_debug(self, download_mock: MagicMock, clone_mock: MagicMock):
        """An entry with both git-repo and url-* is cloned in Debug, downloaded
        (or skipped) in Release."""
        test_config = {
            "content": [
                {
                    "name": "hybrid",
                    "git-repo": "hybrid_repo",
                    "git-ref": "hybrid_ref",
                    "url": "https://example.com/prebuilt.zip",
                }
            ]
        }
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.DEBUG)
        clone_mock.assert_called_once_with("hybrid", "hybrid_repo", "hybrid_ref", None)
        download_mock.assert_not_called()

    @patch("create_libpack.clone")
    @patch("create_libpack.download")
    def test_hybrid_entry_downloads_in_release(
        self, download_mock: MagicMock, clone_mock: MagicMock
    ):
        test_config = {
            "content": [
                {
                    "name": "hybrid",
                    "git-repo": "hybrid_repo",
                    "git-ref": "hybrid_ref",
                    "url": "https://example.com/prebuilt.zip",
                }
            ]
        }
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE)
        download_mock.assert_called_once_with("hybrid", "https://example.com/prebuilt.zip")
        clone_mock.assert_not_called()

    @patch("os.chdir")
    @patch("subprocess.run")
    def test_clone_calls_git_with_ref(self, run_mock: MagicMock, _):
        """When given a repo and a ref, git clone is set up appropriately"""
        create_libpack.clone("name", "https://some.url", "some_git_ref")
        call_data: list = run_mock.call_args_list[0][0][0]
        self.assertIn("https://some.url", call_data)
        self.assertIn("some_git_ref", call_data)
        self.assertIn("--depth", call_data)
        self.assertEqual(call_data[-1], "name")

    @patch("os.chdir")
    @patch("subprocess.run")
    def test_clone_calls_git_without_ref(self, run_mock: MagicMock, _):
        """When given a repo and a ref, git clone is set up appropriately"""
        create_libpack.clone("test", "https://some.url")
        call_data = run_mock.call_args_list[0][0][0]
        self.assertNotIn(None, call_data)
        self.assertNotIn("--branch", call_data)

    def test_build_vswhere_args_latest(self):
        """The 'latest' value uses vswhere's -latest flag and no -version flag."""
        args = create_libpack.build_vswhere_args("latest")
        self.assertIn("-latest", args)
        self.assertNotIn("-version", args)

    def test_build_vswhere_args_friendly_alias(self):
        """A friendly alias like '2022' translates to a vswhere -version range."""
        args = create_libpack.build_vswhere_args("2022")
        self.assertIn("-version", args)
        idx = args.index("-version")
        self.assertEqual(args[idx + 1], "[17.0,18.0)")
        self.assertNotIn("-latest", args)

    def test_build_vswhere_args_raw_range_passthrough(self):
        """A raw vswhere range string is forwarded verbatim."""
        args = create_libpack.build_vswhere_args("[16.0,17.0)")
        idx = args.index("-version")
        self.assertEqual(args[idx + 1], "[16.0,17.0)")

    @patch("os.chdir")
    @patch("subprocess.run")
    def test_clone_qt_skips_submodule_init(self, run_mock: MagicMock, _):
        """Qt's supermodule has many submodules we do not build, and its configure.bat
        initializes only the ones we need, so the clone path skips submodule init."""
        create_libpack.clone("qt", "https://qt.url", "v6.11.0")
        for call in run_mock.call_args_list:
            args = call[0][0]
            self.assertNotIn("submodule", args)

    @patch("subprocess.run")
    def test_exception_is_caught_and_calls_exit(self, run_mock: MagicMock):
        """When given a repo and a ref, git clone is set up appropriately"""
        run_mock.side_effect = CalledProcessError(1, "command_that_was_called")
        with self.assertRaises(SystemExit):
            create_libpack.clone("some_name", "https://some.url")

    def _populate(self, name: str):
        os.makedirs(os.path.join(self.temp_dir.name, name))
        with open(os.path.join(self.temp_dir.name, name, "some_file"), "w", encoding="utf-8") as f:
            f.write("contents")

    @patch("create_libpack.clone")
    def test_skips_existing_paths_with_flag(self, clone_mock: MagicMock):
        test_config = {
            "content": [
                {"name": "test1", "git-repo": "test1_repo", "git-ref": "test1_ref"},
                {"name": "test2", "git-repo": "test2_repo", "git-ref": "test2_ref"},
                {"name": "test3", "git-repo": "test3_repo", "git-ref": "test3_ref"},
            ]
        }
        for item in test_config["content"]:
            self._populate(item["name"])
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE, skip_existing=True)
        clone_mock.assert_not_called()

    @patch("create_libpack.clone")
    def test_interrupted_fetch_is_retried(self, clone_mock: MagicMock):
        """A fetch directory left behind by an attempt that never finished is discarded
        and fetched again, rather than being mistaken for a completed fetch."""
        test_config = {"content": [{"name": "test1", "git-repo": "test1_repo"}]}
        self._populate("test1")
        marker = os.path.join(self.temp_dir.name, create_libpack._fetch_marker_path("test1"))
        with open(marker, "w", encoding="utf-8") as f:
            f.write("test1")
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE, skip_existing=True)
        clone_mock.assert_called_once()
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir.name, "test1")))

    @patch("create_libpack.download")
    def test_empty_download_directory_is_retried(self, download_mock: MagicMock):
        """A download that created its directory and then failed leaves an empty
        directory, which must not satisfy the skip-existing check."""
        test_config = {"content": [{"name": "test1", "url": "https://some.url/test.7z"}]}
        os.makedirs(os.path.join(self.temp_dir.name, "test1"))
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE, skip_existing=True)
        download_mock.assert_called_once()

    @patch("create_libpack.clone")
    def test_empty_directory_is_kept_when_nothing_is_fetched(self, clone_mock: MagicMock):
        """Entries handled elsewhere, by pip for example, legitimately own an empty
        directory, so emptiness alone does not force a re-fetch."""
        test_config = {"content": [{"name": "test1"}]}
        os.makedirs(os.path.join(self.temp_dir.name, "test1"))
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE, skip_existing=True)
        clone_mock.assert_not_called()

    @patch("create_libpack.clone")
    def test_marker_is_cleared_by_a_successful_fetch(self, _):
        test_config = {"content": [{"name": "test1", "git-repo": "test1_repo"}]}
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE)
        marker = os.path.join(self.temp_dir.name, create_libpack._fetch_marker_path("test1"))
        self.assertFalse(os.path.exists(marker))

    @patch("create_libpack.clone")
    def test_marker_survives_a_failed_fetch(self, clone_mock: MagicMock):
        clone_mock.side_effect = SystemExit(1)
        test_config = {"content": [{"name": "test1", "git-repo": "test1_repo"}]}
        with in_directory(self.temp_dir.name):
            with self.assertRaises(SystemExit):
                create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE)
        marker = os.path.join(self.temp_dir.name, create_libpack._fetch_marker_path("test1"))
        self.assertTrue(os.path.exists(marker))

    @patch("create_libpack.download")
    def test_url_calls_download(self, download_mock: MagicMock):
        test_config = {"content": [{"name": "test", "url": "https://some.url"}]}
        with in_directory(self.temp_dir.name):
            create_libpack.fetch_remote_data(test_config, BuildMode.RELEASE)
        download_mock.assert_called_once()

    @patch("requests.get")
    @patch("create_libpack.decompress")
    def test_download_creates_file(self, decompress_mock: MagicMock, get_mock: MagicMock):
        get_mock.return_value = FakeResponse(b"payload")
        with in_directory(self.temp_dir.name):
            create_libpack.download("make_this_dir", "https://some.url/test.7z")
            with open(os.path.join("make_this_dir", "test.7z"), "rb") as f:
                self.assertEqual(f.read(), b"payload")
        decompress_mock.assert_called_once_with("make_this_dir", "test.7z")

    @patch("time.sleep", MagicMock())
    @patch("builtins.print", MagicMock())
    @patch("requests.get")
    @patch("create_libpack.decompress")
    def test_download_retries_a_failed_transfer(
        self, decompress_mock: MagicMock, get_mock: MagicMock
    ):
        get_mock.side_effect = [
            requests.ConnectionError("no route to host"),
            FakeResponse(b"payload"),
        ]
        with in_directory(self.temp_dir.name):
            create_libpack.download("make_this_dir", "https://some.url/test.7z")
        self.assertEqual(get_mock.call_count, 2)
        decompress_mock.assert_called_once_with("make_this_dir", "test.7z")

    @patch("time.sleep", MagicMock())
    @patch("builtins.print", MagicMock())
    @patch("requests.get")
    @patch("create_libpack.decompress")
    def test_truncated_download_is_not_decompressed(
        self, decompress_mock: MagicMock, get_mock: MagicMock
    ):
        """A transfer that ends early must not be handed to the decompressor, and must
        not leave a directory behind for a later run to mistake for a good download."""
        get_mock.return_value = FakeResponse(b"payload", content_length="9999")
        with in_directory(self.temp_dir.name):
            with self.assertRaises(SystemExit):
                create_libpack.download("make_this_dir", "https://some.url/test.7z")
            self.assertFalse(os.path.exists("make_this_dir"))
        decompress_mock.assert_not_called()

    @patch("requests.get")
    @patch("create_libpack.decompress")
    def test_download_discards_a_stale_directory(self, _, get_mock: MagicMock):
        """Assets left over from a previous attempt are removed before the retry, so a
        partial archive cannot end up alongside the fresh one."""
        get_mock.return_value = FakeResponse(b"payload")
        with in_directory(self.temp_dir.name):
            os.makedirs("make_this_dir")
            with open(os.path.join("make_this_dir", "partial.7z"), "wb") as f:
                f.write(b"junk")
            create_libpack.download("make_this_dir", "https://some.url/test.7z")
            self.assertEqual(sorted(os.listdir("make_this_dir")), ["test.7z"])

    @patch("os.chdir")
    @patch("subprocess.run")
    def test_decompress_calls_subprocess(self, run_mock: MagicMock, chdir_mock: MagicMock):
        create_libpack.decompress("path_to_file", "file_name")
        run_mock.assert_called_once()
        self.assertEqual(chdir_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
