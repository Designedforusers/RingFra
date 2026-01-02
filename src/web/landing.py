"""
Landing page template.
"""

from src.config import settings

def render_landing_page() -> str:
    """Render the landing page."""
    phone = settings.TWILIO_PHONE_NUMBER or "Not configured"
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Voice Agent - Manage Infrastructure by Phone</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #fff;
        }}
        .hero {{
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 40px 20px;
            text-align: center;
        }}
        .logo {{
            font-size: 72px;
            margin-bottom: 20px;
        }}
        h1 {{
            font-size: 48px;
            margin-bottom: 20px;
            background: linear-gradient(90deg, #00d4ff, #7b2ff7);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .tagline {{
            font-size: 24px;
            color: rgba(255, 255, 255, 0.8);
            margin-bottom: 40px;
            max-width: 600px;
        }}
        .phone-box {{
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 40px 60px;
            margin-bottom: 40px;
        }}
        .phone-label {{
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 2px;
            color: rgba(255, 255, 255, 0.6);
            margin-bottom: 10px;
        }}
        .phone-number {{
            font-size: 36px;
            font-family: monospace;
            letter-spacing: 2px;
            background: linear-gradient(90deg, #00d4ff, #7b2ff7);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .cta {{
            display: inline-block;
            padding: 18px 40px;
            background: linear-gradient(135deg, #00d4ff, #7b2ff7);
            border-radius: 10px;
            color: #fff;
            font-size: 18px;
            font-weight: 600;
            text-decoration: none;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .cta:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 40px rgba(0, 212, 255, 0.4);
        }}
        .features {{
            display: flex;
            gap: 40px;
            margin-top: 60px;
            flex-wrap: wrap;
            justify-content: center;
        }}
        .feature {{
            text-align: center;
            max-width: 200px;
        }}
        .feature-icon {{
            font-size: 36px;
            margin-bottom: 15px;
        }}
        .feature-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 8px;
        }}
        .feature-desc {{
            font-size: 14px;
            color: rgba(255, 255, 255, 0.6);
        }}
        .demo {{
            margin-top: 80px;
            padding: 30px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 15px;
            max-width: 500px;
        }}
        .demo-title {{
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 2px;
            color: rgba(255, 255, 255, 0.5);
            margin-bottom: 20px;
        }}
        .demo-line {{
            display: flex;
            margin-bottom: 15px;
            align-items: flex-start;
        }}
        .demo-speaker {{
            font-size: 12px;
            font-weight: 600;
            color: #00d4ff;
            width: 50px;
            flex-shrink: 0;
        }}
        .demo-text {{
            color: rgba(255, 255, 255, 0.8);
            font-size: 15px;
        }}
        .footer {{
            margin-top: 60px;
            color: rgba(255, 255, 255, 0.4);
            font-size: 14px;
        }}
        .footer a {{
            color: rgba(255, 255, 255, 0.6);
        }}
    </style>
</head>
<body>
    <div class="hero">
        <div class="logo">📞</div>
        <h1>Voice Agent</h1>
        <p class="tagline">Manage your infrastructure with a phone call. Deploy, scale, fix bugs, check logs — just by talking.</p>
        
        <div class="phone-box">
            <div class="phone-label">Call Now</div>
            <div class="phone-number">{phone}</div>
        </div>
        
        <a href="/signup" class="cta">Connect Your GitHub & Render</a>
        
        <div class="features">
            <div class="feature">
                <div class="feature-icon">🚀</div>
                <div class="feature-title">Deploy</div>
                <div class="feature-desc">"Deploy my API to production"</div>
            </div>
            <div class="feature">
                <div class="feature-icon">📊</div>
                <div class="feature-title">Monitor</div>
                <div class="feature-desc">"What's the CPU usage?"</div>
            </div>
            <div class="feature">
                <div class="feature-icon">🐛</div>
                <div class="feature-title">Fix Bugs</div>
                <div class="feature-desc">"Fix the login bug and call me back"</div>
            </div>
            <div class="feature">
                <div class="feature-icon">📜</div>
                <div class="feature-title">Logs</div>
                <div class="feature-desc">"Show me errors from the last hour"</div>
            </div>
        </div>
        
        <div class="demo">
            <div class="demo-title">Example Conversation</div>
            <div class="demo-line">
                <div class="demo-speaker">YOU</div>
                <div class="demo-text">"Scale up the API, it's getting slow"</div>
            </div>
            <div class="demo-line">
                <div class="demo-speaker">AGENT</div>
                <div class="demo-text">"I'll scale your API from 1 to 2 instances. Done — it should be ready in about 30 seconds."</div>
            </div>
            <div class="demo-line">
                <div class="demo-speaker">YOU</div>
                <div class="demo-text">"Thanks. Also fix that auth bug and call me back when it's done."</div>
            </div>
            <div class="demo-line">
                <div class="demo-speaker">AGENT</div>
                <div class="demo-text">"Got it. I'll work on the auth bug and call you back when it's fixed."</div>
            </div>
        </div>
        
        <div class="footer">
            Built with Render, Twilio, and Claude
        </div>
    </div>
</body>
</html>"""
