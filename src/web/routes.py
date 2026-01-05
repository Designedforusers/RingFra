"""
Web routes for signup and OAuth flows.

NOTE: These routes are scaffolding for multi-tenant mode where users can
sign up via web and connect their GitHub/Render accounts. Not used in
the current single-tenant implementation but kept for future expansion.
"""

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger

from src.config import settings
from src.db.users import get_user_by_phone, create_user, save_user_credentials, get_or_create_user
from src.web.templates import (
    render_signup_page,
    render_connect_github_page,
    render_select_repos_page,
    render_connect_render_page,
    render_success_page,
    render_error_page,
)

router = APIRouter(prefix="/signup", tags=["signup"])

# Store OAuth state tokens temporarily (in production, use Redis)
_oauth_states: dict[str, dict] = {}


@router.get("", response_class=HTMLResponse)
async def signup_page(request: Request, phone: str | None = None):
    """
    Signup landing page.
    
    If phone is provided (from SMS link), pre-fill it.
    """
    return render_signup_page(phone=phone)


@router.post("/start", response_class=HTMLResponse)
async def start_signup(request: Request):
    """
    Start the signup process.
    
    Creates user record and redirects to GitHub OAuth.
    """
    form = await request.form()
    phone = form.get("phone", "").strip()
    
    # Validate phone format (basic check)
    if not phone.startswith("+") or len(phone) < 10:
        return render_error_page("Please enter a valid phone number in international format (e.g., +14155551234)")
    
    # Check if user already exists
    existing = await get_user_by_phone(phone)
    if existing:
        # User exists, go to settings/reconnect page
        return render_connect_github_page(phone=phone, user_exists=True)
    
    # Create new user
    user = await create_user(phone)
    logger.info(f"Created new user {user['id']} for phone {phone}")
    
    return render_connect_github_page(phone=phone, user_exists=False)


@router.get("/github/start")
async def github_oauth_start(phone: str):
    """
    Start GitHub OAuth flow.
    """
    if not settings.GITHUB_CLIENT_ID:
        return render_error_page("GitHub OAuth not configured. Please contact support.")
    
    # Generate state token
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {"phone": phone}
    
    # Build GitHub OAuth URL
    params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": f"{settings.APP_BASE_URL}/signup/github/callback",
        "scope": "repo read:user user:email",
        "state": state,
    }
    
    github_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    return RedirectResponse(url=github_url)


@router.get("/github/callback", response_class=HTMLResponse)
async def github_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    """
    GitHub OAuth callback.
    
    Exchange code for token and save credentials.
    """
    if error:
        return render_error_page(f"GitHub authorization failed: {error}")
    
    if not code or not state:
        return render_error_page("Invalid OAuth callback - missing code or state")
    
    # Verify state
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        return render_error_page("Invalid or expired OAuth state. Please try again.")
    
    phone = state_data["phone"]
    
    # Exchange code for token
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": settings.GITHUB_CLIENT_ID,
                    "client_secret": settings.GITHUB_CLIENT_SECRET,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            token_data = response.json()
    except Exception as e:
        logger.error(f"GitHub token exchange failed: {e}")
        return render_error_page("Failed to connect to GitHub. Please try again.")
    
    if "error" in token_data:
        return render_error_page(f"GitHub error: {token_data.get('error_description', token_data['error'])}")
    
    access_token = token_data.get("access_token")
    if not access_token:
        return render_error_page("No access token received from GitHub")
    
    # Get user info from GitHub
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            response.raise_for_status()
            github_user = response.json()
    except Exception as e:
        logger.error(f"Failed to get GitHub user info: {e}")
        github_user = {}
    
    # Get user's repos
    repos = []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/user/repos?per_page=100&sort=updated",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            response.raise_for_status()
            repos = response.json()
    except Exception as e:
        logger.error(f"Failed to get user repos: {e}")
    
    # Save credentials
    user = await get_or_create_user(phone)
    await save_user_credentials(
        user_id=user["id"],
        provider="github",
        access_token=access_token,
        refresh_token=token_data.get("refresh_token"),
    )
    
    # Store token temporarily for repo selection
    _oauth_states[f"repos_{phone}"] = {"access_token": access_token, "user_id": user["id"]}
    
    logger.info(f"Saved GitHub credentials for user {user['id']} (GitHub: {github_user.get('login', 'unknown')})")
    
    # Show repo selection page
    return render_select_repos_page(
        phone=phone,
        github_username=github_user.get("login"),
        repos=repos,
    )


@router.post("/repos/save", response_class=HTMLResponse)
async def save_selected_repos(request: Request):
    """
    Save selected repositories.
    """
    form = await request.form()
    phone = form.get("phone", "").strip()
    selected_repos = form.getlist("repos")
    
    if not phone:
        return render_error_page("Missing phone number")
    
    # Get stored data
    state_data = _oauth_states.get(f"repos_{phone}")
    if not state_data:
        return render_error_page("Session expired. Please start over.")
    
    user_id = state_data["user_id"]
    
    # Save selected repos
    from src.db.users import add_user_repo
    for repo_url in selected_repos:
        try:
            await add_user_repo(
                user_id=user_id,
                github_url=repo_url,
            )
        except Exception as e:
            logger.error(f"Failed to save repo {repo_url}: {e}")
    
    logger.info(f"Saved {len(selected_repos)} repos for user {user_id}")
    
    # Clean up
    _oauth_states.pop(f"repos_{phone}", None)
    
    # Continue to Render setup
    return render_connect_render_page(phone=phone, repo_count=len(selected_repos))


@router.post("/render/save", response_class=HTMLResponse)
async def save_render_credentials(request: Request):
    """
    Save Render API key.
    """
    form = await request.form()
    phone = form.get("phone", "").strip()
    render_api_key = form.get("render_api_key", "").strip()
    
    if not phone:
        return render_error_page("Missing phone number")
    
    if not render_api_key:
        return render_error_page("Please enter your Render API key")
    
    # Validate the API key by making a test request
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.render.com/v1/services",
                headers={"Authorization": f"Bearer {render_api_key}"},
                timeout=10.0,
            )
            if response.status_code == 401:
                return render_error_page("Invalid Render API key. Please check and try again.")
            response.raise_for_status()
    except httpx.TimeoutException:
        return render_error_page("Render API is not responding. Please try again.")
    except Exception as e:
        logger.error(f"Render API validation failed: {e}")
        return render_error_page("Could not validate Render API key. Please try again.")
    
    # Save credentials
    user = await get_or_create_user(phone)
    await save_user_credentials(
        user_id=user["id"],
        provider="render",
        access_token=render_api_key,
    )
    
    logger.info(f"Saved Render credentials for user {user['id']}")
    
    return render_success_page(phone=phone)


@router.get("/success", response_class=HTMLResponse)
async def success_page(phone: str | None = None):
    """
    Success page after completing signup.
    """
    return render_success_page(phone=phone)
