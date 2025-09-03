@echo off
echo 🚀 AI Auto-Posting Project - Fly.io Deployment Script
echo =====================================================

REM Check if fly CLI is installed
fly --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Fly CLI not found. Installing...
    echo Please install Fly CLI manually: https://fly.io/docs/hands-on/install-flyctl/
    echo After installation, restart your terminal and run this script again.
    pause
    exit /b 1
)

echo ✅ Fly CLI is ready

REM Check if user is logged in to Fly.io
fly auth whoami >nul 2>&1
if %errorlevel% neq 0 (
    echo 🔐 Please login to Fly.io first:
    echo    fly auth login
    pause
    exit /b 1
)

echo ✅ Fly CLI is ready and authenticated

REM Check if app exists
fly apps list | findstr "ai-auto-posting" >nul 2>&1
if %errorlevel% neq 0 (
    echo 📱 Creating new Fly.io app...
    fly apps create ai-auto-posting --org personal
) else (
    echo ✅ App 'ai-auto-posting' already exists
)

REM Check if PostgreSQL database exists
fly postgres list | findstr "ai-auto-posting-db" >nul 2>&1
if %errorlevel% neq 0 (
    echo 🗄️ Creating PostgreSQL database...
    fly postgres create ai-auto-posting-db --org personal --region lhr
) else (
    echo ✅ PostgreSQL database already exists
)

REM Attach database to app
echo 🔗 Attaching database to app...
fly postgres attach ai-auto-posting-db --app ai-auto-posting

REM Create volume for persistent storage
fly volumes list | findstr "ai_auto_posting_data" >nul 2>&1
if %errorlevel% neq 0 (
    echo 💾 Creating persistent volume...
    fly volumes create ai_auto_posting_data --size 3 --region lhr
) else (
    echo ✅ Persistent volume already exists
)

REM Deploy the application
echo 🚀 Deploying to Fly.io with remote builder...
fly deploy --remote-only --yes -a ai-auto-posting

echo.
echo 🎉 Deployment complete!
echo.
echo Your app is now live at: https://ai-auto-posting.fly.dev
echo.
echo Next steps:
echo 1. Set environment variables in Fly.io dashboard
echo 2. Configure your database connection
echo 3. Test your application
echo.
echo Useful commands:
echo   fly logs -a ai-auto-posting          # View logs
echo   fly status -a ai-auto-posting        # Check status
echo   fly open -a ai-auto-posting          # Open in browser
echo   fly destroy ai-auto-posting           # Delete app (if needed)
echo.
pause
