"""
Print the .env lines that turn on the staff dashboard login.

    .\\.venv\\Scripts\\python.exe tools\\hash_password.py

Asks for the password twice (nothing is echoed or stored) and prints:

    DASHBOARD_PASSWORD_HASH=scrypt:16384:8:1:...   the scrypt hash, never the password
    DASHBOARD_SESSION_SECRET=...                    signs the login cookie, so a restart
                                                    doesn't log staff out

Paste both into .env and restart the server. Run it again to change the
password; every existing login stops working when the hash changes.
"""

import getpass
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402

MIN_LENGTH = 10


def main() -> int:
    password = getpass.getpass("Dashboard password: ")
    if len(password) < MIN_LENGTH:
        print(f"Use at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Again: ") != password:
        print("The two passwords don't match.", file=sys.stderr)
        return 1
    print()
    print(f"DASHBOARD_PASSWORD_HASH={auth.hash_password(password)}")
    print(f"DASHBOARD_SESSION_SECRET={secrets.token_urlsafe(32)}")
    print()
    print("Add both lines to .env, then restart the server and open /dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
