import json
from unittest.mock import MagicMock, patch, mock_open
import pytest

# Import prune_backups directly from scripts.backup
import sys
sys.path.append(".")
from scripts.backup import prune_backups

@patch("os.path.exists")
@patch("os.listdir")
@patch("os.path.isdir")
@patch("shutil.rmtree")
def test_prune_backups_retention_logic(mock_rmtree, mock_isdir, mock_listdir, mock_exists):
    mock_exists.return_value = True
    mock_isdir.return_value = True
    
    # We simulate having 4 backups in the directory:
    # 2 db-only and 2 full, sorted oldest to newest:
    # 1. mara-backup-20260601_100000 (db-only)
    # 2. mara-backup-20260602_100000 (full)
    # 3. mara-backup-20260603_100000 (db-only)
    # 4. mara-backup-20260604_100000 (full)
    mock_listdir.return_value = [
        "mara-backup-20260601_100000",
        "mara-backup-20260602_100000",
        "mara-backup-20260603_100000",
        "mara-backup-20260604_100000",
        "some_other_folder"
    ]
    
    # Mocking manifest file exists for all
    # Manifest JSON mock content:
    # 20260601_100000: storage_skipped = True (db-only)
    # 20260602_100000: storage_skipped = False (full)
    # 20260603_100000: storage_skipped = True (db-only)
    # 20260604_100000: storage_skipped = False (full)
    manifest_data = {
        "mara-backup-20260601_100000": {"storage_skipped": True},
        "mara-backup-20260602_100000": {"storage_skipped": False},
        "mara-backup-20260603_100000": {"storage_skipped": True},
        "mara-backup-20260604_100000": {"storage_skipped": False},
    }
    
    original_open = open
    def mock_open_file(filepath, *args, **kwargs):
        for name, data in manifest_data.items():
            if name in filepath and "manifest.json" in filepath:
                return mock_open(read_data=json.dumps(data))()
        return original_open(filepath, *args, **kwargs)
        
    with patch("builtins.open", mock_open_file):
        # We test with retention limits: db-only = 1, full = 1
        # This means:
        # - Keep newest db-only: mara-backup-20260603_100000, delete: mara-backup-20260601_100000
        # - Keep newest full: mara-backup-20260604_100000, delete: mara-backup-20260602_100000
        prune_backups(
            base_dir="./backups",
            db_limit=1,
            full_limit=1,
            disable_prune=False
        )
        
        # Verify shutil.rmtree called on the correct directories
        deleted_paths = [args[0] for args, _ in mock_rmtree.call_args_list]
        
        # Should delete the older db-only backup (20260601_100000)
        assert any("20260601_100000" in path for path in deleted_paths)
        # Should delete the older full backup (20260602_100000)
        assert any("20260602_100000" in path for path in deleted_paths)
        
        # Should NOT delete the newest ones
        assert not any("20260603_100000" in path for path in deleted_paths)
        assert not any("20260604_100000" in path for path in deleted_paths)


@patch("os.path.exists")
@patch("os.listdir")
@patch("shutil.rmtree")
def test_prune_backups_disable_prune(mock_rmtree, mock_listdir, mock_exists):
    # If disable_prune=True, it should return immediately without doing listdir
    prune_backups(
        base_dir="./backups",
        db_limit=1,
        full_limit=1,
        disable_prune=True
    )
    mock_listdir.assert_not_called()
    mock_rmtree.assert_not_called()
