from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.auth import User, UserRole
from ..schemas.auth import LoginRequest, TokenResponse, UserCreate, UserOut
from ..services.auth_service import hash_password, verify_password, create_access_token, get_current_user, require_role, log_audit
from ..config import settings

router = APIRouter(prefix="/api/auth", tags=["Authentication & Access Control"])

@router.post("/login", response_model=TokenResponse)
def login(creds: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == creds.username).first()
    if not user or not verify_password(creds.password, user.password_hash):
        log_audit(db, action="LOGIN_FAILED", details=f"Failed login attempt for username '{creds.username}'")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is currently inactive. Contact system administrator."
        )

    token = create_access_token(user)
    log_audit(db, action="LOGIN_SUCCESS", user=user, details="User logged in successfully")

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.TOKEN_EXPIRE_HOURS * 3600,
        user=user.to_dict()
    )

@router.get("/me")
def get_profile(current_user: User = Depends(get_current_user)):
    return current_user.to_dict()

@router.post("/logout")
def logout(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    log_audit(db, action="LOGOUT", user=current_user, details="User logged out")
    return {"message": "Logged out successfully"}

@router.get("/users", dependencies=[Depends(require_role([UserRole.ADMIN, UserRole.SUPERVISOR]))])
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    return [u.to_dict() for u in users]

@router.post("/register-user", dependencies=[Depends(require_role([UserRole.ADMIN]))])
def register_user(user_in: UserCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    existing = db.query(User).filter(User.username == user_in.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    new_user = User(
        username=user_in.username,
        password_hash=hash_password(user_in.password),
        full_name=user_in.full_name,
        role=user_in.role,
        phone_number=user_in.phone_number,
        village=user_in.village,
        sub_centre=user_in.sub_centre,
        primary_health_centre=user_in.primary_health_centre,
        preferred_language=user_in.preferred_language or "ta",
        is_active=True
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    log_audit(db, action="CREATE_USER", user=current_user, target_entity="user", target_id=str(new_user.id), details=f"Created user {new_user.username} with role {new_user.role}")
    return new_user.to_dict()

