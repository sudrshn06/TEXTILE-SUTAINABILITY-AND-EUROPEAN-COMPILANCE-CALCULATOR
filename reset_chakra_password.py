import sqlite3
import getpass

from yugam.app import PASSWORD_HASHER, DB_PATH


email = input("Account email: ").strip()

if not email:
    print("Email is required.")
    raise SystemExit

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

user = con.execute(
    "SELECT id, email, role, factory FROM users WHERE email=? COLLATE NOCASE",
    (email,)
).fetchone()

if not user:
    print("USER NOT FOUND")
    con.close()
    raise SystemExit

print("Account:", user["email"])
print("Role:", user["role"])
print("Factory:", user["factory"])

password = getpass.getpass("New password: ")
confirm = getpass.getpass("Confirm password: ")

if password != confirm:
    print("Passwords do not match.")
    con.close()
    raise SystemExit

if not password:
    print("Password cannot be empty.")
    con.close()
    raise SystemExit

new_hash = PASSWORD_HASHER.hash(password)

con.execute("""
UPDATE users
SET password_hash=?,
    failed_attempts=0,
    locked_until=0,
    active=1
WHERE id=?
""", (new_hash, user["id"]))

con.execute(
    "DELETE FROM sessions WHERE user_id=?",
    (user["id"],)
)

con.commit()
con.close()

print("PASSWORD RESET SUCCESSFUL")