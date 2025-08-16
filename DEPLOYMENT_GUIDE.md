# 🚀 Railway Deployment Guide for AI Auto-Posting Project

## 📋 Prerequisites
- GitHub account
- Railway account (free at [railway.app](https://railway.app))
- Your project ready with the deployment files

## 🎯 Step-by-Step Deployment

### Step 1: Prepare Your Project ✅
Your project is now ready with:
- `Procfile` - Tells Railway how to run your app
- `railway.json` - Railway configuration
- `runtime.txt` - Python version specification
- `server_production.py` - Production-optimized server
- `requirements_production.txt` - Production dependencies

### Step 2: Push to GitHub
```bash
git add .
git commit -m "Add Railway deployment configuration"
git push origin main
```

### Step 3: Connect to Railway
1. Go to [railway.app](https://railway.app)
2. Sign up/Login with GitHub
3. Click "New Project"
4. Select "Deploy from GitHub repo"
5. Choose your repository

### Step 4: Configure Environment Variables
In Railway dashboard, add these variables:
- `SECRET_KEY` - Generate a random secret key
- `GOOGLE_API_KEY` - Your Google Gemini API key
- `DB_HOST` - Railway MySQL host (will be provided)
- `DB_USER` - Database username
- `DB_PASSWORD` - Database password
- `DB_NAME` - Database name (usually 'automation')

### Step 5: Add MySQL Database
1. In Railway dashboard, click "New"
2. Select "Database" → "MySQL"
3. Railway will automatically connect it to your app
4. Copy the connection details to your environment variables

### Step 6: Deploy
1. Railway will automatically detect your Flask app
2. It will install dependencies from `requirements_production.txt`
3. Start command will use the `Procfile`
4. Your app will be live in minutes!

## 🔧 Configuration Details

### Database Connection
Railway will provide:
- `DB_HOST` - Usually something like `containers-us-west-1.railway.app`
- `DB_PORT` - Usually 3306
- `DB_USER` - Your database username
- `DB_PASSWORD` - Your database password

### Environment Variables Priority
1. Railway dashboard (highest priority)
2. `.env` file (local development)
3. Default values in code

## 📊 Monitoring & Logs

### View Logs
- Go to your Railway project
- Click on your service
- View real-time logs

### Health Check
Your app includes a health check endpoint at `/` that Railway uses to monitor the service.

## 🚨 Troubleshooting

### Common Issues
1. **Build fails**: Check `requirements_production.txt` for compatibility
2. **Database connection**: Verify environment variables
3. **Port issues**: Railway automatically sets `PORT` environment variable
4. **File uploads**: Ensure directories exist and are writable

### Debug Commands
```bash
# Check Railway logs
railway logs

# Check environment variables
railway variables

# Restart service
railway service restart
```

## 🌐 Custom Domain
1. In Railway dashboard, go to "Settings"
2. Add custom domain
3. Railway provides free SSL certificates

## 💰 Cost Management
- Free tier: $5/month credit
- Pay-per-use pricing
- Monitor usage in dashboard

## 🔄 Continuous Deployment
- Every push to main branch triggers automatic deployment
- Railway builds and deploys automatically
- Rollback to previous versions available

## 📱 Your App URL
After deployment, Railway will provide:
- `https://your-app-name.railway.app`
- Custom domain (if configured)
- Automatic HTTPS

## 🎉 Success!
Your AI Auto-Posting project is now:
- ✅ Live on the internet
- ✅ Scalable and production-ready
- ✅ Monitored and managed
- ✅ Easy to update and maintain

## 📞 Support
- Railway documentation: [docs.railway.app](https://docs.railway.app)
- Community: [discord.gg/railway](https://discord.gg/railway)
- Email: waqas56jb@gmail.com
