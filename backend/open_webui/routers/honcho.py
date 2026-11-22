"""Shared memory profile view; native entries are retained separately."""

from fastapi import APIRouter, Depends, HTTPException, Request
from open_webui.utils.auth import get_verified_user
from open_webui.utils.honcho import connector, enrolled

router = APIRouter()


@router.get('/backend')
async def backend(user=Depends(get_verified_user)):
    return {'backend': 'honcho' if enrolled(user) else 'native'}


@router.get('/profile')
async def profile(request: Request, user=Depends(get_verified_user)):
    if not enrolled(user):
        raise HTTPException(403, 'Shared memory is not enabled for this account.')
    return await connector('GET', '/profile', timeout=15)
