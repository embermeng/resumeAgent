from datetime import timedelta, UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status, Response, Cookie
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

import src.database.models as models
from src.auth.security import (
    CurrentUser,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from src.config import get_config
from src.database.database import get_async_session
from src.api.schemas_api import Token, UserCreate, UserLoginReq, UserPrivate, UserPublic, UserUpdate

router = APIRouter()

settings = get_config()

@router.post(
    "/auth/register",
    response_model=UserPrivate,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(user: UserCreate, db: Annotated[AsyncSession, Depends(get_async_session)]):
    result = await db.execute(
        select(models.User).where(
            func.lower(models.User.username) == user.username.lower(),
        ),
    )
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    result = await db.execute(
        select(models.User).where(func.lower(
            models.User.email) == user.email.lower()),
    )
    existing_email = result.scalars().first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    new_user = models.User(
        username=user.username,
        email=user.email.lower(),
        password_hash=hash_password(user.password),
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@router.post("/auth/login", response_model=Token)
async def login_for_access_token(
    req: UserLoginReq,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    response: Response,
):
    # Look up user by email (case-insensitive)
    result = await db.execute(
        select(models.User).where(
            func.lower(models.User.email) == req.email.lower(),
        ),
    )
    user = result.scalars().first()

    # Verify user exists and password is correct
    # Don't reveal which one failed (security best practice)
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create access token with user id as subject
    access_token_expires = timedelta(
        minutes=settings.auth.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=access_token_expires,
    )

    family_id = str(uuid.uuid4())
    jti = str(uuid.uuid4())
    refresh_expires_delta = timedelta(
        minutes=settings.auth.refresh_token_expire_minutes)
    refresh_token = create_refresh_token(
        data={"sub": str(user.id), "jti": jti},
        expires_delta=refresh_expires_delta,
    )
    expire_at = datetime.now(UTC) + refresh_expires_delta
    db.add(models.RefreshToken(jti=jti, family_id=family_id,
           user_id=user.id, expire_at=expire_at))
    await db.commit()
    response.set_cookie(
        key="refresh-token",
        value=refresh_token,
        httponly=True,
        path="/api/auth",
        samesite="strict",
        secure=False,
        max_age=settings.auth.refresh_token_expire_minutes * 60,
    )
    return Token(access_token=access_token, token_type="bearer")


@router.post("/auth/refresh", response_model=Token)
async def refresh_token(
    db: Annotated[AsyncSession, Depends(get_async_session)],
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias="refresh-token")] = None
):
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_refresh_token(refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    jti = payload.get("jti")
    sub = int(payload.get("sub"))
    res = await db.execute(select(models.RefreshToken).where(
        models.RefreshToken.jti == jti
    ))
    rt = res.scalars().first()
    if not rt:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # refresh token被吊销了，需要吊销整个family的token
    if rt.revoked:
        revoked_family_id = rt.family_id
        res = await db.execute(select(models.RefreshToken).where(
            models.RefreshToken.family_id == revoked_family_id
        ))
        rts = res.scalars().all()
        for rt in rts:
            rt.revoked = True
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    new_jti = str(uuid.uuid4())
    refresh_expires_delta = timedelta(
        minutes=settings.auth.refresh_token_expire_minutes)
    expire_at = datetime.now(UTC) + refresh_expires_delta
    new_rt = models.RefreshToken(jti=new_jti, family_id=rt.family_id, user_id=rt.user_id, expire_at=expire_at)
    rt.revoked = True
    db.add(new_rt)
    await db.commit()
    access_token_expires = timedelta(
        minutes=settings.auth.access_token_expire_minutes)
    new_access_token = create_access_token(
        data={"sub": str(sub)},
        expires_delta=access_token_expires,
    )
    new_refresh_token = create_refresh_token(
        data={"sub": str(sub), "jti": new_jti},
        expires_delta=refresh_expires_delta,
    )
    response.set_cookie(
        key="refresh-token",
        value=new_refresh_token,
        httponly=True,
        path="/api/auth",
        samesite="strict",
        secure=False,
        max_age=settings.auth.refresh_token_expire_minutes * 60,
    )
    return Token(access_token=new_access_token, token_type="bearer")


@router.get("/auth/me", response_model=UserPrivate)
async def get_current_user(current_user: CurrentUser):
    return current_user


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    db: Annotated[AsyncSession, Depends(get_async_session)],
    refresh_token: Annotated[str | None, Cookie(alias="refresh-token")] = None
):
    if refresh_token:
        payload = decode_refresh_token(refresh_token)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        jti = payload.get("jti")
        res = await db.execute(select(models.RefreshToken.family_id).where(
            models.RefreshToken.jti == jti
        ))
        family_id = res.scalars().first()
        res = await db.execute(select(models.RefreshToken).where(
            models.RefreshToken.family_id == family_id
        ))
        rts = res.scalars().all()
        for rt in rts:
            rt.revoked = True
        await db.commit()


@router.get("/auth/user/{user_id}", response_model=UserPublic)
async def get_user(user_id: int, db: Annotated[AsyncSession, Depends(get_async_session)]):
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if user:
        return user
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="User not found")


@router.patch("/auth/{user_id}", response_model=UserPrivate)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_session)],
):
    if user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user",
        )

    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if (
        user_update.username is not None
        and user_update.username.lower() != user.username.lower()
    ):
        result = await db.execute(
            select(models.User).where(
                func.lower(
                    models.User.username) == user_update.username.lower(),
            ),
        )
        existing_user = result.scalars().first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username already exists",
            )
    if (
        user_update.email is not None
        and user_update.email.lower() != user.email.lower()
    ):
        result = await db.execute(
            select(models.User).where(
                func.lower(models.User.email) == user_update.email.lower(),
            ),
        )
        existing_email = result.scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )

    if user_update.username is not None:
        user.username = user_update.username
    if user_update.email is not None:
        user.email = user_update.email.lower()

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/auth/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_session)],
):
    if user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this user",
        )

    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    await db.delete(user)
    await db.commit()
