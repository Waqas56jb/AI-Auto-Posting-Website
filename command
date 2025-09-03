fly deploy -a ai-auto-posting --remote-only --yes



//api token
# 1) client_secrets.json (required)
$cs = [Convert]::ToBase64String([IO.File]::ReadAllBytes(".\client_secrets.json"))
fly secrets set -a ai-auto-posting CLIENT_SECRETS_JSON_BASE64="$cs"

# 2) static\youtube_token.json (optional if you already completed OAuth)
$yt = [Convert]::ToBase64String([IO.File]::ReadAllBytes(".\static\youtube_token.json"))
fly secrets set -a ai-auto-posting YOUTUBE_TOKEN_JSON_BASE64="$yt"

fly deploy -a ai-auto-posting --remote-only --yes