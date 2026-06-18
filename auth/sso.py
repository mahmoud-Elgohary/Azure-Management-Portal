"""
Entra ID sign-in via MSAL Authorization Code flow.
Sign-in only — openid/profile/email scopes, no Microsoft Graph data calls.
"""

import secrets
from functools import wraps

import msal
from flask import redirect, request, session, url_for

import config

_SCOPES = []  # MSAL adds openid/profile/email automatically; passing them raises ValueError


def _msal_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=config.AUTH_CLIENT_ID,
        client_credential=config.AUTH_CLIENT_SECRET,
        authority=config.AUTHORITY,
    )


def get_auth_url(redirect_uri: str, state: str) -> str:
    return _msal_app().get_authorization_request_url(
        scopes=_SCOPES,
        redirect_uri=redirect_uri,
        state=state,
    )


def get_token_from_code(code: str, redirect_uri: str) -> dict:
    return _msal_app().acquire_token_by_authorization_code(
        code=code,
        scopes=_SCOPES,
        redirect_uri=redirect_uri,
    )


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            if request.method == "GET":
                session["next"] = request.url
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def check_group(token_claims: dict) -> bool:
    """
    Returns True if access is permitted.
    If ALLOWED_GROUP_ID is set, the group must appear in the token's 'groups' claim.
    If ALLOWED_GROUP_ID is blank, any authenticated user is allowed (rely on
    'Assignment required' in the app registration to gate tenant-level access).
    """
    if not config.ALLOWED_GROUP_ID:
        return True
    groups = token_claims.get("groups", [])
    return config.ALLOWED_GROUP_ID in groups
