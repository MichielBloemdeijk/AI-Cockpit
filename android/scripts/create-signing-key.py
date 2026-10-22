"""Create the permanent Jarvis release signing key outside the repository.

Run once with ``cd backend && uv run python ../android/scripts/create-signing-key.py``.
Every published update must be signed with this key; losing it means the phone
must uninstall Jarvis (and re-pair) before it can accept updates again. Back up
both the keystore and the properties file.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess


DEFAULT_PROPERTIES = Path.home() / ".jarvis" / "android-signing.properties"


def keytool() -> str:
    candidates = [Path(os.environ["JAVA_HOME"]) / "bin" / "keytool.exe"] if os.environ.get("JAVA_HOME") else []
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Android/Android Studio/jbr/bin/keytool.exe")
    found = next((str(path) for path in candidates if path.is_file()), None) or shutil.which("keytool")
    if not found:
        raise SystemExit("keytool not found; set JAVA_HOME or install Android Studio")
    return found


def create(properties: Path) -> Path:
    """Write a new keystore beside ``properties``; refuses to replace an existing key."""
    keystore = properties.with_name("jarvis-release.jks")
    if properties.exists() or keystore.exists():
        raise SystemExit(f"Signing key already exists at {keystore}; replacing it would break updates")
    properties.parent.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(24)
    subprocess.run([keytool(), "-genkeypair", "-keystore", str(keystore), "-storetype", "PKCS12",
        "-alias", "jarvis", "-keyalg", "RSA", "-keysize", "4096", "-validity", "36500",
        "-storepass", password, "-keypass", password, "-dname", "CN=Jarvis, O=AI Cockpit"],
        check=True, capture_output=True)
    # Properties files treat backslashes as escapes.
    properties.write_text(f"storeFile={keystore.as_posix()}\nstorePassword={password}\n"
        f"keyAlias=jarvis\nkeyPassword={password}\n", encoding="utf-8")
    return keystore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--properties", type=Path, default=Path(os.environ.get("JARVIS_SIGNING_PROPERTIES", DEFAULT_PROPERTIES)))
    keystore = create(parser.parse_args().properties.resolve())
    print(f"Created {keystore}. Back up this folder: {keystore.parent}")


if __name__ == "__main__":
    main()
