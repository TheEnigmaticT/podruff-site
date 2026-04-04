import click

@click.group()
def cli():
    """Video pipeline: ingest, segment, publish."""
    pass

@cli.command()
@click.argument("url")
def ingest(url):
    """Download and process a single video by URL."""
    click.echo(f"Ingesting: {url}")

@cli.command()
def poll():
    """Poll Notion for new ingest cards and process them."""
    click.echo("Polling Notion for new cards...")

@cli.command()
@click.option("--dry-run", is_flag=True, help="Show what would be published without publishing.")
@click.option("--date", default=None, help="Date to publish for (YYYY-MM-DD). Defaults to today.")
def publish(dry_run, date):
    """Publish scheduled clips via Late API."""
    click.echo(f"Publishing (dry_run={dry_run})...")
