#!/bin/python3

# SPDX-License-Identifier: LGPL-2.1-or-later

import diff_match_patch
import unittest
from unittest.mock import MagicMock, patch, mock_open

import generate_patch

""" Developer tests for the generate_patch module. """

class TestGeneratePatch(unittest.TestCase):

    def setUp(self):
        super().setUp()

    def tearDown(self):
        super().tearDown()

    @patch("sys.argv", ["exe", "old", "new", "patch"])
    def test_command_line_options_count_is_correct(self):
        generate_patch.parse_args()

    @patch("sys.argv", ["exe"])
    def test_command_line_args_missing_is_error(self):
        with self.assertRaises(SystemExit):
            generate_patch.parse_args()

    def test_patch_generated(self):
        # Arrange
        old = "Line1\nLine2\nLine4\n"
        new = "Line1\nLine2\nLine3\n"
        dmp = diff_match_patch.diff_match_patch()
        difference = dmp.patch_toText(dmp.patch_make(old, new))

        # Act
        result = generate_patch.generate_patch(old, new)

        # Assert
        self.assertEqual(result, difference)

    def test_run_loads_all_files(self):
        with patch("builtins.open", mock_open()) as open_mock:
            generate_patch.run("old", "new", "patch")
            expected_calls = [unittest.mock.call("old","r",encoding="utf-8"),
                              unittest.mock.call("new","r",encoding="utf-8"),
                              unittest.mock.call("patch","w",encoding="utf-8")]
            for call in expected_calls:
                self.assertIn(call, open_mock.mock_calls)
