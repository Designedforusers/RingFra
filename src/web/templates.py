"""
Simple HTML templates for signup flow.

Uses inline HTML to avoid template engine dependencies.
"""

from src.config import settings

# Shared styles
STYLES = """
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        min-height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 20px;
        color: #fff;
    }
    .container {
        background: rgba(255, 255, 255, 0.1);
        backdrop-filter: blur(10px);
        border-radius: 20px;
        padding: 40px;
        max-width: 480px;
        width: 100%;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
    }
    h1 {
        font-size: 28px;
        margin-bottom: 10px;
        background: linear-gradient(90deg, #00d4ff, #7b2ff7);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .subtitle {
        color: rgba(255, 255, 255, 0.7);
        margin-bottom: 30px;
        font-size: 16px;
    }
    .step {
        display: flex;
        align-items: center;
        margin-bottom: 20px;
        padding: 15px;
        background: rgba(255, 255, 255, 0.05);
        border-radius: 10px;
    }
    .step-number {
        width: 32px;
        height: 32px;
        background: linear-gradient(135deg, #00d4ff, #7b2ff7);
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        margin-right: 15px;
        flex-shrink: 0;
    }
    .step.done .step-number {
        background: #10b981;
    }
    .step.done .step-number::after {
        content: '✓';
    }
    .step.done .step-number span {
        display: none;
    }
    input[type="text"], input[type="tel"], input[type="password"] {
        width: 100%;
        padding: 15px;
        border: 2px solid rgba(255, 255, 255, 0.2);
        border-radius: 10px;
        background: rgba(255, 255, 255, 0.1);
        color: #fff;
        font-size: 16px;
        margin-bottom: 15px;
        transition: border-color 0.3s;
    }
    input:focus {
        outline: none;
        border-color: #00d4ff;
    }
    input::placeholder {
        color: rgba(255, 255, 255, 0.5);
    }
    button, .btn {
        width: 100%;
        padding: 15px;
        background: linear-gradient(135deg, #00d4ff, #7b2ff7);
        border: none;
        border-radius: 10px;
        color: #fff;
        font-size: 16px;
        font-weight: 600;
        cursor: pointer;
        transition: transform 0.2s, box-shadow 0.2s;
        text-decoration: none;
        display: inline-block;
        text-align: center;
    }
    button:hover, .btn:hover {
        transform: translateY(-2px);
        box-shadow: 0 5px 20px rgba(0, 212, 255, 0.4);
    }
    .btn-secondary {
        background: rgba(255, 255, 255, 0.1);
        border: 2px solid rgba(255, 255, 255, 0.2);
    }
    .error {
        background: rgba(239, 68, 68, 0.2);
        border: 1px solid #ef4444;
        color: #fca5a5;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    .success {
        background: rgba(16, 185, 129, 0.2);
        border: 1px solid #10b981;
        color: #6ee7b7;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    .phone-display {
        font-family: monospace;
        font-size: 24px;
        text-align: center;
        padding: 20px;
        background: rgba(255, 255, 255, 0.1);
        border-radius: 10px;
        margin: 20px 0;
        letter-spacing: 2px;
    }
    .help-text {
        font-size: 14px;
        color: rgba(255, 255, 255, 0.6);
        margin-top: 10px;
    }
    a {
        color: #00d4ff;
        text-decoration: none;
    }
    a:hover {
        text-decoration: underline;
    }
    .logo {
        font-size: 48px;
        margin-bottom: 20px;
    }
</style>
"""


def _base_html(title: str, content: str) -> str:
    """Wrap content in base HTML structure."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Voice Agent</title>
    {STYLES}
</head>
<body>
    <div class="container">
        {content}
    </div>
</body>
</html>"""


def render_signup_page(phone: str | None = None) -> str:
    """Render the initial signup page."""
    phone_value = phone or ""
    
    content = f"""
        <div class="logo">📞</div>
        <h1>Voice Agent Setup</h1>
        <p class="subtitle">Connect your GitHub and Render to manage infrastructure by phone</p>
        
        <form action="/signup/start" method="POST">
            <input 
                type="tel" 
                name="phone" 
                placeholder="+1 (555) 123-4567" 
                value="{phone_value}"
                required
                pattern="\\+[0-9]{{10,15}}"
            >
            <p class="help-text">Enter your phone number in international format</p>
            <br>
            <button type="submit">Get Started</button>
        </form>
    """
    
    return _base_html("Setup", content)


def render_connect_github_page(phone: str, user_exists: bool = False) -> str:
    """Render the GitHub connection page."""
    status_msg = "Welcome back! Reconnect your GitHub to continue." if user_exists else "Great! Now let's connect your GitHub."
    
    content = f"""
        <div class="logo">🔗</div>
        <h1>Connect GitHub</h1>
        <p class="subtitle">{status_msg}</p>
        
        <div class="step">
            <div class="step-number done"><span>1</span></div>
            <div>Phone number verified</div>
        </div>
        
        <div class="step">
            <div class="step-number"><span>2</span></div>
            <div>Connect GitHub</div>
        </div>
        
        <div class="step">
            <div class="step-number"><span>3</span></div>
            <div>Add Render API key</div>
        </div>
        
        <br>
        <a href="/signup/github/start?phone={phone}" class="btn">
            Connect GitHub
        </a>
        <p class="help-text" style="text-align: center; margin-top: 15px;">
            We'll request access to your repositories
        </p>
    """
    
    return _base_html("Connect GitHub", content)


def render_connect_render_page(phone: str, github_username: str | None = None) -> str:
    """Render the Render API key input page."""
    github_msg = f"Connected as <strong>{github_username}</strong>" if github_username else "GitHub connected"
    
    content = f"""
        <div class="logo">🚀</div>
        <h1>Connect Render</h1>
        <p class="subtitle">Almost done! Add your Render API key.</p>
        
        <div class="step done">
            <div class="step-number"><span>1</span></div>
            <div>Phone verified</div>
        </div>
        
        <div class="step done">
            <div class="step-number"><span>2</span></div>
            <div>{github_msg}</div>
        </div>
        
        <div class="step">
            <div class="step-number"><span>3</span></div>
            <div>Add Render API key</div>
        </div>
        
        <br>
        <form action="/signup/render/save" method="POST">
            <input type="hidden" name="phone" value="{phone}">
            <input 
                type="password" 
                name="render_api_key" 
                placeholder="rnd_xxxxxxxxxxxxxxxx"
                required
            >
            <p class="help-text">
                Get your API key from <a href="https://dashboard.render.com/u/settings/api-keys" target="_blank">Render Dashboard → Account Settings → API Keys</a>
            </p>
            <br>
            <button type="submit">Complete Setup</button>
        </form>
    """
    
    return _base_html("Connect Render", content)


def render_success_page(phone: str | None = None) -> str:
    """Render the success page."""
    twilio_number = settings.TWILIO_PHONE_NUMBER or "+1 (415) 853-6485"
    
    content = f"""
        <div class="logo">✅</div>
        <h1>You're All Set!</h1>
        <p class="subtitle">Your voice agent is ready to use.</p>
        
        <div class="success">
            GitHub and Render are connected. You can now manage your infrastructure by phone.
        </div>
        
        <p style="text-align: center; margin-bottom: 10px;">Call this number anytime:</p>
        <div class="phone-display">{twilio_number}</div>
        
        <p class="help-text" style="text-align: center;">
            The agent will recognize your caller ID and load your repos and services automatically.
        </p>
        
        <br>
        <p style="text-align: center; color: rgba(255,255,255,0.6); font-size: 14px;">
            Try saying: "What services are running?" or "Show me the logs for my API"
        </p>
    """
    
    return _base_html("Setup Complete", content)


def render_error_page(message: str) -> str:
    """Render an error page."""
    content = f"""
        <div class="logo">❌</div>
        <h1>Something Went Wrong</h1>
        
        <div class="error">
            {message}
        </div>
        
        <a href="/signup" class="btn btn-secondary">Start Over</a>
    """
    
    return _base_html("Error", content)
