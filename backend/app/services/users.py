from sqlalchemy.orm import Session

from app.models import User
from app.services.auth import Identity


def get_or_create_user(db: Session, identity: Identity) -> User:
    user = db.get(User, identity.uid)
    if user is None:
        user = User(id=identity.uid, email=identity.email)
        db.add(user)
        db.commit()
    return user
