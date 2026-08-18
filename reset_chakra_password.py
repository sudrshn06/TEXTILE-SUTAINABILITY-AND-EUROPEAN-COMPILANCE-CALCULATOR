import sqlite3
import getpass
from yugam.app import PASSWORD_HASHER

email = "security@a.com"

password = getpass.getpass("New password: ")
confirm = getpass.getpass("Confirm password: ")

if password != confirm:
    print("Passwords do not match.")
    raise SystemExit

con = sqlite3.connect(r"yugam\data\chakra_security.db")

new_hash = PASSWORD_HASHER.hash(password)

con.execute("""
UPDATE users
SET password_hash=?,
    failed_attempts=0,
    locked_until=0,
    active=1
WHERE email=?
""", (new_hash, email))

con.execute("""
DELETE FROM sessions
WHERE user_id=(SELECT id FROM users WHERE email=?)
""", (email,))

con.commit()
con.close()

print("PASSWORD RESET SUCCESSFUL")
