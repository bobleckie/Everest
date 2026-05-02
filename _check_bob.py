from app.database import SessionLocal
from app.models import User
from app.auth import verify_password, get_password_hash

db = SessionLocal()
users = db.query(User).all()
for u in users:
    print(f"id={u.id} username={u.username!r} email={u.email!r} role={u.role} active={u.is_active} must_change={getattr(u, 'must_change_password', None)}")

bob = db.query(User).filter(User.username == "Bob").first()
print("Bob found:", bob is not None)
if bob:
    print("verify '12345':", verify_password("12345", bob.hashed_password))
    # Re-set to be safe
    bob.hashed_password = get_password_hash("12345")
    bob.is_active = True
    bob.must_change_password = False
    db.commit()
    print("Re-set password. verify again:", verify_password("12345", bob.hashed_password))
db.close()
