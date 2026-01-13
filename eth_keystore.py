#!/usr/bin/env python3
import os
import sys
import json
from datetime import datetime
from getpass import getpass

from eth_account import Account


KEYSTORE_DIR = "keystore"


def ensure_keystore_dir():
    os.makedirs(KEYSTORE_DIR, exist_ok=True)


def prompt_password(confirm=True):
    pwd1 = getpass("Enter password: ")
    if not pwd1:
        raise ValueError("Password cannot be empty")

    if confirm:
        pwd2 = getpass("Confirm password: ")
        if pwd1 != pwd2:
            raise ValueError("Passwords do not match")

    return pwd1


def load_private_key_from_keystore(keystore_path: str = None, password: str = None) -> str:
    """
    Load private key from keystore file.
    
    Args:
        keystore_path: Path to keystore file. If None, will look for keystore files in KEYSTORE_DIR
        password: Password to decrypt keystore. If None, will prompt for password
    
    Returns:
        Hex-encoded private key string starting with '0x'
    """
    if keystore_path is None:
        # Look for keystore files in the default directory
        ensure_keystore_dir()
        keystore_files = [f for f in os.listdir(KEYSTORE_DIR) if f.startswith('UTC--') and f.endswith('.json')]
        
        if not keystore_files:
            raise FileNotFoundError(f"No keystore files found in {KEYSTORE_DIR}")
        elif len(keystore_files) == 1:
            keystore_path = os.path.join(KEYSTORE_DIR, keystore_files[0])
        else:
            print(f"Multiple keystore files found in {KEYSTORE_DIR}:")
            for i, file in enumerate(keystore_files):
                print(f"{i+1}) {file}")
            choice = input("Select keystore file [1]: ") or "1"
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(keystore_files):
                    keystore_path = os.path.join(KEYSTORE_DIR, keystore_files[idx])
                else:
                    raise ValueError("Invalid selection")
            except ValueError:
                raise ValueError("Invalid selection")
    
    if not os.path.isfile(keystore_path):
        raise FileNotFoundError(f"Keystore file not found: {keystore_path}")
    
    if password is None:
        password = getpass(f"请输入keystore文件密码: {keystore_path}: ")
    
    if not password:
        raise ValueError("Password cannot be empty")
    
    try:
        with open(keystore_path, 'r') as f:
            keystore_data = json.load(f)
        
        private_key_bytes = Account.decrypt(keystore_data, password)
        private_key_hex = private_key_bytes.hex()
        
        return f"0x{private_key_hex}"
        
    except Exception as e:
        raise ValueError(f"Failed to decrypt keystore: {e}") from e


def write_keystore(acct, password):
    keystore = Account.encrypt(
        acct.key,
        password,
        kdf="scrypt"
    )

    address = acct.address.lower().replace("0x", "")
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
    filename = f"UTC--{ts}--{address}.json"

    ensure_keystore_dir()
    path = os.path.join(KEYSTORE_DIR, filename)

    with open(path, "w") as f:
        json.dump(keystore, f, indent=2)

    print("\nWallet created successfully!\n")
    print(f"Address: {acct.address}")
    print(f"Keystore file: {path}\n")

    print("IMPORTANT:")
    print("- Keep this file safe")
    print("- Do NOT forget your password")
    print("- The password cannot be recovered")
    print("- This file can be used with Ethereum tools and this script")


def init_wallet():
    print("This tool will create a new local Ethereum wallet.")
    print("You will be asked to set a password to encrypt it.\n")

    password = prompt_password(confirm=True)
    acct = Account.create()

    write_keystore(acct, password)


def import_private_key():
    print("Importing wallet from private key.")
    print("The private key will NOT be saved.\n")

    pk = getpass("Enter private key (0x...): ").strip()

    if pk.startswith("0x"):
        pk = pk[2:]

    if len(pk) != 64:
        raise ValueError("Invalid private key length")

    try:
        acct = Account.from_key(bytes.fromhex(pk))
    except Exception:
        raise ValueError("Invalid private key format")

    print(f"\nDetected address: {acct.address}\n")

    password = prompt_password(confirm=True)
    write_keystore(acct, password)


def import_keystore():
    print("Importing from existing keystore file.\n")

    path = input("Enter path to keystore file: ").strip()
    if not os.path.isfile(path):
        raise FileNotFoundError("Keystore file not found")

    with open(path) as f:
        old_keystore = json.load(f)

    old_pwd = getpass("Enter existing keystore password: ")

    try:
        private_key = Account.decrypt(old_keystore, old_pwd)
    except Exception:
        raise ValueError("Failed to decrypt keystore (wrong password?)")

    acct = Account.from_key(private_key)

    print(f"\nDetected address: {acct.address}")
    print("A new keystore file will be created with a new password.\n")

    new_pwd = prompt_password(confirm=True)
    write_keystore(acct, new_pwd)


def import_wallet():
    print("How would you like to import your wallet?\n")
    print("1) Private key (0x...)")
    print("2) Existing keystore file\n")

    choice = input("Select an option [1-2]: ").strip()

    if choice == "1":
        import_private_key()
    elif choice == "2":
        import_keystore()
    else:
        raise ValueError("Invalid selection")


def usage():
    print("Usage:")
    print("  eth-keystore init      Create a new wallet")
    print("  eth-keystore import    Import an existing wallet")
    sys.exit(1)


def main():
    if len(sys.argv) != 2:
        usage()

    cmd = sys.argv[1]

    try:
        if cmd == "init":
            init_wallet()
        elif cmd == "import":
            import_wallet()
        else:
            usage()
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
