"""
Rotating the screenshot encryption key must not destroy the screenshots.

WHY THIS MATTERS, AND WHY IT IS NOT ACADEMIC. A key was committed to this
repository's history once. The repository is public — deliberately, so the
code can be read — so anything that was ever in it is readable for ever.
The answer to that is to rotate the key.

Except rotation was not possible. decrypt_bytes knew exactly one key, the
current one, so changing SCREENSHOT_ENCRYPTION_KEY made every screenshot
already captured answer "AES-GCM decryption failed". Rotating meant
destroying the evidence the key was protecting — so it does not get done,
and a compromised key stays in service. A key you cannot afford to change is
a key you are stuck with.

With SCREENSHOT_ENCRYPTION_KEY_RETIRED, rotation is a restart: new captures
use the new key from that moment, everything already stored still opens, and
when the retention window has passed the last file the old key made, the old
key is dropped and gone.

Run:  python3 tests/test_key_rotation.py
"""
import base64
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if not ok and detail else ""))
    if not ok:
        failures += 1


workspace = tempfile.mkdtemp(prefix="ets-rotation-")
OLD = base64.b64encode(os.urandom(32)).decode()
NEW = base64.b64encode(os.urandom(32)).decode()
STRANGER = base64.b64encode(os.urandom(32)).decode()
BLOB = os.path.join(workspace, "shot.enc")


def run(code, **keys):
    """A fresh interpreter per step — the key is read at import time."""
    env = {**os.environ, "ETS_DATA_DIR": workspace, "QT_QPA_PLATFORM": "offscreen"}
    env.pop("SCREENSHOT_ENCRYPTION_KEY_RETIRED", None)
    env.update({k: v for k, v in keys.items() if v is not None})
    done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=env, cwd=ROOT)
    return (done.stdout + done.stderr).strip()


print("A screenshot taken before the rotation")
out = run(f"""
import sys; sys.path.insert(0, {ROOT!r})
from client.security.crypto_engine import CryptoEngine
open({BLOB!r}, 'wb').write(CryptoEngine.encrypt_bytes(b'evidence'))
print('written')
""", SCREENSHOT_ENCRYPTION_KEY=OLD)
check("is written with the key of the day", "written" in out, out[-120:])

print("\nRotating WITHOUT keeping the old key — the loss this prevents")
out = run(f"""
import sys; sys.path.insert(0, {ROOT!r})
from client.security.crypto_engine import CryptoEngine
try:
    CryptoEngine.decrypt_bytes(open({BLOB!r}, 'rb').read()); print('READ')
except Exception as error:
    print('LOST', error)
""", SCREENSHOT_ENCRYPTION_KEY=NEW)
check("the new key alone cannot read it", out.startswith("LOST"), out[-120:])

print("\nRotating WITH the old key retired")
out = run(f"""
import sys; sys.path.insert(0, {ROOT!r})
from client.security.crypto_engine import CryptoEngine
print(CryptoEngine.decrypt_bytes(open({BLOB!r}, 'rb').read()).decode())
fresh = CryptoEngine.encrypt_bytes(b'taken after')
print(CryptoEngine.decrypt_bytes(fresh).decode())
""", SCREENSHOT_ENCRYPTION_KEY=NEW, SCREENSHOT_ENCRYPTION_KEY_RETIRED=OLD)
check("the old screenshot still opens", "evidence" in out, out[-160:])
check("and new ones are written with the NEW key", "taken after" in out, out[-160:])

print("\nA key that made none of these files")
out = run(f"""
import sys; sys.path.insert(0, {ROOT!r})
from client.security.crypto_engine import CryptoEngine
try:
    print('READ', CryptoEngine.decrypt_bytes(open({BLOB!r}, 'rb').read()))
except Exception as error:
    print('REFUSED')
""", SCREENSHOT_ENCRYPTION_KEY=NEW, SCREENSHOT_ENCRYPTION_KEY_RETIRED=STRANGER)
check("is refused, not guessed at", out.startswith("REFUSED"), out[-120:])
# AES-GCM carries its own tag, which is what makes trying keys in turn safe:
# a wrong key CANNOT return plausible-looking rubbish, it fails.

print("\nMore than one retired key, and a malformed one")
out = run(f"""
import sys; sys.path.insert(0, {ROOT!r})
from client.security.crypto_engine import CryptoEngine
print(CryptoEngine.decrypt_bytes(open({BLOB!r}, 'rb').read()).decode())
""", SCREENSHOT_ENCRYPTION_KEY=NEW,
     SCREENSHOT_ENCRYPTION_KEY_RETIRED=f"not-base64!!, {STRANGER}, {OLD}")
check("a list is tried in turn, and a typo in it is skipped",
      "evidence" in out,
      "a bad entry in the list must not stop the good one being reached")

print("\nA key written the way the documentation says")
# THE TRAP THIS CLOSES. .env.example said "64 hex characters: openssl rand
# -hex 32" while the loader base64-decoded it. Hex digits are valid base64
# characters, so the decode SUCCEEDED, produced 48 bytes, and the app refused
# to start with "Encryption key must be 32 bytes" — a message that says
# nothing about the format. Following the instructions gave you an
# application that would not open, in the middle of a key rotation.
import binascii
HEX = binascii.hexlify(os.urandom(32)).decode()
out = run(f"""
import sys; sys.path.insert(0, {ROOT!r})
from client.security.crypto_engine import CryptoEngine
print(CryptoEngine.decrypt_bytes(CryptoEngine.encrypt_bytes(b'hex key')).decode())
""", SCREENSHOT_ENCRYPTION_KEY=HEX)
check("a hex key works", "hex key" in out, out[-140:])

out = run(f"""
import sys; sys.path.insert(0, {ROOT!r})
from client.security.crypto_engine import CryptoEngine
print(CryptoEngine.decrypt_bytes(CryptoEngine.encrypt_bytes(b'b64 key')).decode())
""", SCREENSHOT_ENCRYPTION_KEY=base64.b64encode(os.urandom(32)).decode())
check("a base64 key works", "b64 key" in out, out[-140:])

out = run(f"""
import sys; sys.path.insert(0, {ROOT!r})
from client.security.crypto_engine import CryptoEngine
CryptoEngine.encrypt_bytes(b'x')
""", SCREENSHOT_ENCRYPTION_KEY="obviously-not-a-key")
check("and something that is neither says so, in those words",
      "hex" in out and "base64" in out,
      "the old message named a length and left you guessing at the format")

import shutil
shutil.rmtree(workspace, ignore_errors=True)
print("\nall key rotation checks passed" if failures == 0 else f"\n{failures} FAILED")
sys.exit(0 if failures == 0 else 1)
