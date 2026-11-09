"""A keyring backend that keeps passwords in one JSON file, for tests only, so they never touch the OS password safe.

    PYTHON_KEYRING_BACKEND=camrig.keyring_file.FileKeyring  CAMRIG_TEST_KEYRING_DIR=<a temporary folder>

Every call also appends its name to asked.txt in that folder, so a test can prove the safe was never asked.
"""
import json
import os
from pathlib import Path

from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class FileKeyring(KeyringBackend):
    priority = 1

    def folder(self, call: str) -> Path:
        d = Path(os.environ["CAMRIG_TEST_KEYRING_DIR"])      # no default: a missing folder must fail, never guess
        with open(d / "asked.txt", "a") as f:
            f.write(call + "\n")
        return d

    def load(self, d: Path) -> dict:
        f = d / "keyring.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def get_password(self, service, username):
        return self.load(self.folder("get")).get(f"{service}\n{username}")

    def set_password(self, service, username, password):
        d = self.folder("set")
        data = self.load(d)
        data[f"{service}\n{username}"] = password
        (d / "keyring.json").write_text(json.dumps(data))

    def delete_password(self, service, username):
        d = self.folder("delete")
        data = self.load(d)
        if data.pop(f"{service}\n{username}", None) is None:
            raise PasswordDeleteError("no such password")
        (d / "keyring.json").write_text(json.dumps(data))
