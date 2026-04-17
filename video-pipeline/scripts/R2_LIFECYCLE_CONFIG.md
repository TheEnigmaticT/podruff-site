# R2 Bucket Lifecycle Configuration

## Status: COMPLETED

The Cloudflare R2 bucket `video-pipeline` has been configured with a lifecycle rule to automatically delete objects older than 90 days.

### Current Lifecycle Rules

Two lifecycle rules are now active on the bucket:

1. **Default Multipart Abort Rule** (system default)
   - Aborts incomplete multipart uploads after 7 days
   - Prefix: (all prefixes)
   - Status: Enabled

2. **auto-delete-90-days** (newly added)
   - Expires objects after 90 days
   - Prefix: (all prefixes)
   - Status: Enabled

### Configuration Details

- **Account ID:** 17b0dc3bc288a8bdb1c7cc94eb88f70a
- **Bucket Name:** video-pipeline
- **Expiration Policy:** Auto-delete objects after 90 days
- **Applied:** 2026-04-14

### How It Works

All objects uploaded to the `video-pipeline` R2 bucket will be automatically deleted after 90 days of creation. This helps control storage costs and ensures temporary video processing artifacts don't accumulate indefinitely.

### Management

To view or modify lifecycle rules:

```bash
# List all lifecycle rules
wrangler r2 bucket lifecycle list video-pipeline

# Add a new rule
wrangler r2 bucket lifecycle add video-pipeline <name> <prefix> --expire-days <days>

# Remove a rule
wrangler r2 bucket lifecycle remove video-pipeline <name>
```

### Why Wrangler?

- R2 API credentials (provided via .env) have S3-level permissions but lack lifecycle management scope
- Wrangler CLI is Cloudflare's official R2 management tool and has full lifecycle support
- Authentication is handled via OAuth token already configured on this machine
- Commands are simple and consistent with Cloudflare's documentation

### Alternative Approaches (Not Used)

1. **Cloudflare REST API** - Would require a Cloudflare API token with explicit lifecycle permissions
2. **boto3 S3-compatible API** - R2 credentials lack the necessary IAM permissions for lifecycle management

### References

- [Cloudflare R2 Documentation](https://developers.cloudflare.com/r2/)
- [Wrangler R2 Commands](https://developers.cloudflare.com/workers/wrangler/commands/#r2)
- [Lifecycle Rules Overview](https://developers.cloudflare.com/r2/buckets/object-expiration/)
