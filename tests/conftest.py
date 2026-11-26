"""Test-wide settings: closed-place reports and accounts go to throwaway folders, never the app's real data."""
import os
import tempfile

os.environ.setdefault("AZ_CLOSED_REPORTS", os.path.join(tempfile.mkdtemp(prefix="az-closed-"), "closed_reports.json"))
os.environ.setdefault("AZ_STORE_DIR", tempfile.mkdtemp(prefix="az-accounts-"))
os.environ.pop("AZ_STORE_REPO", None)
os.environ.pop("AZ_STORE_TOKEN", None)
