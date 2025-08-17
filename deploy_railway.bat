@echo off
echo 🚂 AI Auto-Posting Project - Railway Deployment Script
echo =====================================================

REM Check if Railway CLI is installed
railway --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Railway CLI not found. Installing...
    echo Installing Railway CLI via npm...
    npm install -g @railway/cli
    if %errorlevel% neq 0 (
        echo ❌ Failed to install Railway CLI. Please install Node.js first.
        echo Download from: https://nodejs.org/
        pause
        exit /b 1
    )
)

echo ✅ Railway CLI is ready

REM Check if user is logged in to Railway
railway whoami >nul 2>&1
if %errorlevel% neq 0 (
    echo 🔐 Please login to Railway first:
    echo    railway login
    pause
    exit /b 1
)

echo ✅ Railway CLI is ready and authenticated

REM Initialize Railway project if not already done
if not exist "railway.toml" (
    echo 📱 Initializing Railway project...
    railway init
) else (
    echo ✅ Railway project already initialized
)

REM Deploy the application
echo 🚂 Deploying to Railway...
railway up

echo.
echo 🎉 Deployment complete!
echo.
echo Your app is now live on Railway!
echo.
echo Next steps:
echo 1. Set environment variables in Railway dashboard
echo 2. Configure your database connection
echo 3. Test your application
echo.
echo Useful commands:
echo   railway logs                    # View logs
echo   railway status                  # Check status
echo   railway open                    # Open in browser
echo   railway destroy                 # Delete project (if needed)
echo.
pause
