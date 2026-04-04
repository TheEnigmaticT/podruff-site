# HeyGen Pipeline Setup Guide

## Prerequisites

- Node.js 18+ installed
- HeyGen API key (get from https://app.heygen.com/settings/api-key)

## Step-by-Step Setup

### 1. API Key Setup

Your API key should already be at `~/.config/heygen/api_key`. If not:

```bash
mkdir -p ~/.config/heygen
echo "your-api-key-here" > ~/.config/heygen/api_key
```

### 2. Configuration

Copy the example config:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` and set your defaults:

```yaml
defaults:
  avatar_id: "YOUR_AVATAR_ID"
  voice_id: "YOUR_VOICE_ID"
```

### 3. Find Your Avatar and Voice IDs

```bash
# List all available avatars
node src/cli.js avatars

# List all available voices
node src/cli.js voices
```

Copy the IDs you want to use into your `config.yaml`.

### 4. Test Your Setup

Create a test video:

```bash
node src/cli.js create \
  -s "This is a test video to confirm everything is working!" \
  --wait
```

If successful, you'll see:
- Video ID generated
- Status updates
- Download confirmation
- File saved to `./videos/`

### 5. Ready to Use!

Your pipeline is now ready. Try:

```bash
# Create a single video
node src/cli.js create -s "Hello world!"

# Process a batch
node src/cli.js batch -f examples/batch-social-media.json

# Check your videos
node src/cli.js list
```

## Configuration Options

### Client-Specific Settings

Add clients to `config.yaml`:

```yaml
clients:
  crowdtamers:
    name: "CrowdTamers"
    default_avatar: "ct-avatar-id"
    default_voice: "ct-voice-id"
  
  clientname:
    name: "Client Name"
    default_avatar: "client-avatar-id"
    default_voice: "client-voice-id"
```

### Language Settings

Add or modify languages:

```yaml
languages:
  supported:
    - code: "it"
      name: "Italian"
      default_voice: "it-IT-ElsaNeural"
```

### Storage Settings

Change where videos are stored:

```yaml
storage:
  video_dir: ./my-videos
  metadata_dir: ./my-metadata
  max_local_videos: 50
```

### Queue Settings

Adjust processing behavior:

```yaml
queue:
  concurrency: 3        # Process 3 videos at once
  max_retries: 5        # Retry failed jobs 5 times
  retry_delay: 60       # Wait 60 seconds between retries
```

## Common Setup Issues

### "Cannot find module 'commander'"

```bash
npm install
```

### "API key file not found"

Check the path:

```bash
cat ~/.config/heygen/api_key
```

If empty or missing, add your key.

### "Failed to create video: 401"

Your API key may be invalid. Verify at:
https://app.heygen.com/settings/api-key

### "Avatar ID is required"

Either:
1. Set `defaults.avatar_id` in `config.yaml`, or
2. Use `--avatar <id>` flag on every command

## Next Steps

- Read the main [README.md](README.md) for full documentation
- Check [examples/](examples/) for batch file templates
- Review [templates/](templates/) for video structure guidance
