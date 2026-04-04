# 🎬 CrowdTamers HeyGen Video Pipeline

A complete video generation pipeline for creating AI avatar videos at scale. Built for CrowdTamers to produce client ad campaigns, internal content, and multi-language video variations.

## Features

- ✅ **HeyGen API Integration** - Direct integration with HeyGen v2 API
- ✅ **Script-to-Video** - Convert text scripts to professional AI avatar videos
- ✅ **Multi-Language Support** - Generate videos in multiple languages with localized voices
- ✅ **Client Management** - Organize videos by client and campaign
- ✅ **Batch Processing** - Queue and process multiple videos efficiently
- ✅ **Video Storage** - Local storage with metadata tracking
- ✅ **CLI Interface** - Easy-to-use command-line interface

## Quick Start

### 1. Installation

```bash
# Navigate to the pipeline directory
cd heygen-pipeline

# Install dependencies
npm install

# Copy example config
cp config.example.yaml config.yaml
```

### 2. Configuration

Edit `config.yaml` with your settings:

```yaml
# Required: Set your default avatar and voice
defaults:
  avatar_id: "your-avatar-id"
  voice_id: "your-voice-id"

# Optional: Add client configurations
clients:
  myclient:
    name: "My Client"
    default_avatar: "client-avatar-id"
    default_voice: "client-voice-id"
```

Find your avatar and voice IDs:
```bash
node src/cli.js avatars
node src/cli.js voices
```

### 3. Create Your First Video

```bash
# Simple video
node src/cli.js create -s "Hello, this is my first AI video!" --wait

# With specific avatar and voice
node src/cli.js create \
  -s "Welcome to our product demo!" \
  -a "Daisy-SitCasual-20241210" \
  -v "en-US-JennyNeural" \
  --wait
```

## Commands Reference

### Create Single Video

```bash
node src/cli.js create [options]

Options:
  -s, --script <text>     Script text or path to file (required)
  -a, --avatar <id>       Avatar ID
  -v, --voice <id>        Voice ID
  -l, --language <code>   Language code (default: "en")
  --width <pixels>        Video width (default: 1920)
  --height <pixels>       Video height (default: 1080)
  --client <name>         Client name for organization
  --campaign <name>       Campaign name
  --caption               Add captions
  --wait                  Wait for completion and download
```

### Batch Processing

```bash
# Process multiple videos from JSON file
node src/cli.js batch -f examples/batch-multilanguage.json

# With custom concurrency
node src/cli.js batch -f my-batch.json --concurrency 3
```

### List Videos

```bash
# List all videos
node src/cli.js list

# Filter by client
node src/cli.js list --client crowdtamers

# Filter by language
node src/cli.js list --language es
```

### Check Status

```bash
# Check specific video
node src/cli.js status <video-id>

# Check all pending jobs
node src/cli.js status

# Check by job ID
node src/cli.js status --job <job-id>
```

### Download Video

```bash
node src/cli.js download <video-id>
```

### Browse Assets

```bash
# List available avatars
node src/cli.js avatars

# List available voices
node src/cli.js voices
```

### Reports & Maintenance

```bash
# Generate storage report
node src/cli.js report

# Clean up old videos
node src/cli.js cleanup --days 30

# Keep only 50 most recent
node src/cli.js cleanup --max 50
```

## Batch File Format

Create a JSON file for batch processing:

```json
{
  "name": "My Campaign",
  "client": "clientname",
  "campaign": "campaign-id",
  "defaultAvatar": "avatar-id",
  "defaultVoice": "voice-id",
  "videos": [
    {
      "id": "video-1",
      "language": "en",
      "script": "Your script here...",
      "avatar": "optional-override",
      "voice": "optional-override",
      "width": 1920,
      "height": 1080,
      "caption": true
    }
  ]
}
```

## Multi-Language Workflows

### Creating Translated Content

1. Create your base script in English
2. Create a batch file with translations
3. Run batch processing

Example batch file for multi-language:
```json
{
  "name": "Product Launch - Global",
  "targetLanguages": ["en", "es", "fr", "de"],
  "videos": [
    {
      "id": "launch-en",
      "language": "en",
      "script": "Introducing our new product..."
    },
    {
      "id": "launch-es",
      "language": "es",
      "script": "Presentamos nuestro nuevo producto..."
    }
  ]
}
```

### Supported Languages

- English (en)
- Spanish (es)
- French (fr)
- German (de)
- Portuguese (pt)
- Japanese (ja)
- Chinese (zh)

Add more in `config.yaml` under `languages.supported`.

## Video Templates

Available templates in the `templates/` folder:

| Template | Use Case | Dimensions |
|----------|----------|------------|
| `product-demo` | Product demonstrations | 1920x1080 |
| `social-media-hook` | TikTok/Reels/Shorts | 1080x1920 |
| `testimonial` | Customer testimonials | 1920x1080 |
| `announcement` | Company announcements | 1920x1080 |

## Directory Structure

```
heygen-pipeline/
├── src/
│   ├── cli.js              # Command-line interface
│   ├── heygen-client.js    # API client
│   ├── queue.js            # Queue management
│   ├── storage.js          # Video storage
│   └── language.js         # Multi-language support
├── examples/               # Example batch files
├── templates/              # Video templates
├── videos/                 # Downloaded videos (created)
├── metadata/               # Video metadata (created)
├── config.yaml            # Your configuration
└── package.json
```

## Client-Specific Workflows

### Adding a New Client

1. Add client config to `config.yaml`:

```yaml
clients:
  newclient:
    name: "New Client Inc"
    default_avatar: "their-avatar-id"
    default_voice: "their-voice-id"
    brand_colors:
      primary: "#FF0000"
```

2. Create client-specific batch files in `examples/`

3. Run with `--client newclient` flag

### Organizing by Campaign

Use the `--campaign` flag to group videos:

```bash
node src/cli.js create \
  -s "Holiday sale announcement" \
  --client myclient \
  --campaign holiday-2024
```

## Troubleshooting

### "Avatar ID is required"

Set a default avatar in `config.yaml` or use `--avatar` flag:

```bash
node src/cli.js avatars  # List available avatars
```

### Video stuck "processing"

HeyGen videos typically take 1-5 minutes. Check status:

```bash
node src/cli.js status <video-id>
```

### API Key Issues

Ensure your API key is at `~/.config/heygen/api_key`:

```bash
mkdir -p ~/.config/heygen
echo "your-api-key" > ~/.config/heygen/api_key
```

### Batch job failed

Check job details:

```bash
node src/cli.js status --job <job-id>
```

Failed jobs automatically retry up to 3 times.

## Tips for Best Results

### Script Writing

- **Keep it concise** - 150 words ≈ 1 minute
- **Use natural pauses** - Add commas for breathing room
- **Avoid special characters** - They can cause issues
- **Test first** - Create one video before batch processing

### Avatar Selection

- Match avatar style to brand (professional, casual, etc.)
- Consider your target audience demographics
- Test different avatars to see what resonates

### Voice Selection

- Choose voices that match your brand tone
- Test different accents for regional campaigns
- Neural voices sound more natural

### Video Dimensions

| Platform | Dimensions | Aspect Ratio |
|----------|------------|--------------|
| YouTube | 1920x1080 | 16:9 |
| TikTok/Reels | 1080x1920 | 9:16 |
| LinkedIn | 1920x1080 | 16:9 |
| Instagram Feed | 1080x1080 | 1:1 |

## API Reference

The pipeline uses HeyGen API v2:

- Base URL: `https://api.heygen.com`
- Documentation: https://docs.heygen.com/

## Contributing

To add new features:

1. Edit the relevant module in `src/`
2. Update documentation
3. Add examples if applicable

## License

MIT - CrowdTamers Internal Use
