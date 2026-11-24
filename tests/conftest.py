"""Test-wide settings: closed-place reports go to a throwaway file, never the app's real list."""
import os
import tempfile

os.environ.setdefault("AZ_CLOSED_REPORTS", os.path.join(tempfile.mkdtemp(prefix="az-closed-"), "closed_reports.json"))
