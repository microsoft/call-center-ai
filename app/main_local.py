"""
Jay's Frames AI Assistant - Local Development Server

This is a simplified version for local testing without Azure services.
For production deployment, see the full app/main.py
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
import yaml
from pathlib import Path

# Load configuration
def load_config():
    """Load configuration from YAML file."""
    config_path = Path("config-jays-frames-example.yaml")
    if config_path.exists():
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    return {
        "conversation": {
            "initiate": {
                "bot_company": "Jay's Frames",
                "bot_name": "Jordan"
            }
        }
    }

CONFIG = load_config()

# Create FastAPI app
app = FastAPI(
    title="Jay's Frames AI Assistant - Local Dev",
    description="24/7 AI-powered phone system for custom art framing (Development Mode)",
    version="1.0.0-local",
)

@app.get("/")
async def root():
    """Root endpoint with welcome message."""
    bot_company = CONFIG.get("conversation", {}).get("initiate", {}).get("bot_company", "Jay's Frames")
    bot_name = CONFIG.get("conversation", {}).get("initiate", {}).get("bot_name", "Jordan")

    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{bot_company} AI Assistant</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 900px;
                margin: 50px auto;
                padding: 20px;
                line-height: 1.6;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
            }}
            .container {{
                background: white;
                padding: 40px;
                border-radius: 10px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            }}
            h1 {{ color: #2c3e50; margin-top: 0; }}
            .status {{
                color: #27ae60;
                font-weight: bold;
                font-size: 1.2em;
                margin: 20px 0;
            }}
            .warning {{
                background: #fff3cd;
                border: 1px solid #ffc107;
                padding: 15px;
                border-radius: 5px;
                margin: 20px 0;
                color: #856404;
            }}
            .info {{
                background: #f8f9fa;
                padding: 20px;
                border-radius: 5px;
                margin: 20px 0;
                border-left: 4px solid #667eea;
            }}
            .endpoint {{
                background: #e9ecef;
                padding: 12px;
                margin: 8px 0;
                border-radius: 3px;
                font-family: 'Monaco', 'Courier New', monospace;
                font-size: 0.9em;
            }}
            .endpoint a {{ color: #3498db; text-decoration: none; }}
            .endpoint a:hover {{ text-decoration: underline; }}
            .method {{
                display: inline-block;
                padding: 2px 8px;
                border-radius: 3px;
                font-weight: bold;
                margin-right: 8px;
                font-size: 0.8em;
            }}
            .get {{ background: #61affe; color: white; }}
            .post {{ background: #49cc90; color: white; }}
            ul {{ padding-left: 25px; }}
            li {{ margin: 10px 0; }}
            .feature {{
                display: inline-block;
                margin: 5px;
                padding: 5px 12px;
                background: #e3f2fd;
                border-radius: 15px;
                font-size: 0.9em;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎨 {bot_company} AI Assistant</h1>
            <p class="status">✓ Development Server Running</p>

            <div class="warning">
                <strong>⚠️ Development Mode</strong><br>
                This is a local development server. Full features require Azure deployment.
            </div>

            <div class="info">
                <h2>Configuration</h2>
                <p><strong>Business:</strong> {bot_company}</p>
                <p><strong>Bot Name:</strong> {bot_name}</p>
                <p><strong>Mode:</strong> Local Development (No Azure services)</p>
            </div>

            <h2>Features (When Deployed to Azure)</h2>
            <div>
                <span class="feature">📞 Phone Calls (24/7)</span>
                <span class="feature">🎙️ Speech Recognition</span>
                <span class="feature">🤖 AI Conversations (GPT-4)</span>
                <span class="feature">💬 SMS Summaries</span>
                <span class="feature">🌐 Multi-language</span>
                <span class="feature">📊 Call Analytics</span>
                <span class="feature">🎨 Custom Framing Expertise</span>
            </div>

            <h2>Available Endpoints</h2>
            <div class="endpoint">
                <span class="method get">GET</span>
                <a href="/health/liveness">/health/liveness</a> - Health check
            </div>
            <div class="endpoint">
                <span class="method get">GET</span>
                <a href="/health/readiness">/health/readiness</a> - Readiness check
            </div>
            <div class="endpoint">
                <span class="method get">GET</span>
                <a href="/config">/config</a> - View configuration
            </div>
            <div class="endpoint">
                <span class="method get">GET</span>
                <a href="/info">/info</a> - System information
            </div>
            <div class="endpoint">
                <span class="method get">GET</span>
                <a href="/docs">/docs</a> - Interactive API Documentation
            </div>
            <div class="endpoint">
                <span class="method get">GET</span>
                <a href="/redoc">/redoc</a> - ReDoc Documentation
            </div>

            <h2>Next Steps</h2>
            <ol>
                <li>✅ <strong>Local server is running!</strong></li>
                <li>📖 Review the configuration in <code>config-jays-frames-example.yaml</code></li>
                <li>📚 Read the deployment guides:
                    <ul>
                        <li><code>JAYS_FRAMES_DEPLOYMENT_GUIDE.md</code></li>
                        <li><code>JAYS_FRAMES_README.md</code></li>
                    </ul>
                </li>
                <li>☁️ Set up Azure resources (see deployment guide)</li>
                <li>🚀 Deploy to production</li>
            </ol>

            <h2>Documentation</h2>
            <ul>
                <li><a href="/docs">Interactive API Docs (Swagger UI)</a></li>
                <li><a href="/redoc">Alternative API Docs (ReDoc)</a></li>
                <li>Setup Checklist: <code>JAYS_FRAMES_SETUP_CHECKLIST.md</code></li>
                <li>Production Readiness: <code>PRODUCTION_READINESS_ASSESSMENT.md</code></li>
            </ul>

            <div class="info" style="margin-top: 30px;">
                <h3>💡 Quick Tip</h3>
                <p>To deploy this for real phone calls:</p>
                <ol>
                    <li>Create Azure account (free trial available)</li>
                    <li>Follow the deployment guide</li>
                    <li>Purchase a phone number ($1-2/month)</li>
                    <li>Total setup time: ~1 hour</li>
                    <li>Monthly cost: $185-650 based on call volume</li>
                </ol>
            </div>
        </div>
    </body>
    </html>
    """)

@app.get("/health/liveness")
async def liveness():
    """Health check - is the service running?"""
    return {"status": "healthy", "service": "jays-frames-ai-local"}

@app.get("/health/readiness")
async def readiness():
    """Readiness check - is the service ready?"""
    return {
        "status": "ready",
        "mode": "development",
        "azure_services": "not_connected",
        "local_only": True,
    }

@app.get("/config")
async def get_config():
    """View current configuration (safe fields only)."""
    return {
        "business": CONFIG.get("conversation", {}).get("initiate", {}),
        "mode": "local_development",
        "azure_required_for_production": True,
    }

@app.get("/info")
async def get_info():
    """System information."""
    return {
        "name": "Jay's Frames AI Assistant",
        "version": "1.0.0-local",
        "mode": "development",
        "status": "running",
        "features": {
            "phone_calls": False,  # Requires Azure
            "speech_recognition": False,  # Requires Azure
            "ai_conversations": False,  # Requires Azure OpenAI
            "sms": False,  # Requires Azure
            "local_api": True,
        },
        "next_steps": [
            "Review configuration files",
            "Read deployment documentation",
            "Set up Azure account",
            "Deploy to production"
        ]
    }

@app.exception_handler(404)
async def not_found(request: Request, exc):
    """Handle 404 errors."""
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not found",
            "path": str(request.url.path),
            "tip": "Visit / for available endpoints"
        }
    )

if __name__ == "__main__":
    import uvicorn
    print("🎨 Starting Jay's Frames AI Assistant (Local Development)")
    print("📍 Server will run at: http://localhost:8080")
    print("📖 API Docs available at: http://localhost:8080/docs")
    uvicorn.run(app, host="0.0.0.0", port=8080)
