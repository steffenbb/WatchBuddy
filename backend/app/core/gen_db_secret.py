import secrets
import string
import os

import argparse

DEFAULT_SECRET_PATH = "/run/secrets/db_creds.txt"

def generate(target_path: str = DEFAULT_SECRET_PATH):

    """Generate DB password and write to target_path. User/db are static (watchbuddy)."""
    pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))

    dirpath = os.path.dirname(target_path)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)

    with open(target_path, "w") as f:
        f.write(f"{pw}\n")

    print("WARNING: Generated DB password is sensitive. Do NOT commit this file to git.")
    print(f"Wrote password to {target_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate DB creds into a secrets file")
    parser.add_argument("--out", "-o", default=os.getenv("DB_SECRET_PATH", DEFAULT_SECRET_PATH),
                        help="Target path for the generated secret file (default /run/secrets/db_creds.txt)")
    args = parser.parse_args()
    generate(args.out)
