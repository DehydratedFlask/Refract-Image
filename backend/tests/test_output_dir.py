"""Destination handling for a run.

The app lets the user choose where images go, so the folder a run writes to is either the
service's default or one they picked. Both cases have to be settled *before* the model
loads: finding out that a folder is missing, read-only or actually a file after a
five-minute denoise wastes the run, so the check is asserted here rather than left to the
first write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refract_backend.paths import OutputDirError, prepare_output_dir


def test_default_output_dir_is_the_services_own(isolated_dirs, monkeypatch):
    monkeypatch.setenv("REFRACT_OUTPUT_DIR", str(isolated_dirs / "default-outputs"))
    target = prepare_output_dir(None)
    assert target == isolated_dirs / "default-outputs"
    assert target.is_dir()


def test_chosen_output_dir_wins_and_is_created(tmp_path):
    chosen = tmp_path / "Pictures" / "Refract"
    target = prepare_output_dir(str(chosen))
    assert target == chosen
    assert target.is_dir()
    # The write probe must not be left behind in the user's folder.
    assert list(target.iterdir()) == []


def test_a_chosen_folder_is_used_verbatim(tmp_path):
    """Whatever the user picked is what the run writes to, trailing slash and all."""
    chosen = tmp_path / "not" / "the" / "default" / "place"
    assert Path(prepare_output_dir(str(chosen) + "/")) == chosen


def test_unwritable_destination_fails_before_the_model_loads(tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("this path is a file, so a child directory cannot exist")

    with pytest.raises(OutputDirError) as failure:
        prepare_output_dir(str(blocker / "sub"))

    message = str(failure.value)
    assert "Cannot save images to" in message
    assert str(blocker / "sub") in message
    # The user needs a way out of the error, so the message says where.
    assert "Settings" in message


def test_read_only_destination_is_rejected_not_discovered_later(tmp_path):
    """A folder that exists but cannot be written to fails the write probe."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(OutputDirError):
            prepare_output_dir(str(locked))
    finally:
        locked.chmod(0o700)
