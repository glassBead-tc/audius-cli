"""
A command-line interface for interacting with the Audius web3 streaming platform
using its public API. This CLI supports both the REST API and GraphQL subgraph.
The REST API provides access to content (tracks, playlists, users) while the
GraphQL subgraph provides access to on-chain governance and staking data.

Each command prints the JSON response from the API to stdout. On HTTP
errors a message is printed to stderr describing the failure.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import requests

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

DEFAULT_BASE_URL: str = "https://discoveryprovider.audius.co/v1"
DEFAULT_GRAPHQL_ENDPOINT: str = "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/F8TjrYuTLohz64J8uuDke9htSR1aY9TGCuEjJVVjUJaD"
CONFIG_DIR: Path = Path.home() / ".audius-cli"
CONFIG_FILE: Path = CONFIG_DIR / "config.yaml"
CACHE_DIR: Path = CONFIG_DIR / "cache"


def _load_config() -> Dict[str, Any]:
    """Load configuration from config file if it exists."""
    if not CONFIG_FILE.exists():
        return {}
    
    if not HAS_YAML:
        return {}
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = yaml.safe_load(f) or {}
        return config
    except Exception as e:
        click.echo(f"Warning: Could not load config file: {e}", err=True)
        return {}


def _get_cache_key(url: str, params: List[tuple]) -> str:
    """Generate cache key from URL and parameters."""
    cache_str = url + str(sorted(params))
    return hashlib.md5(cache_str.encode()).hexdigest()


def _get_from_cache(cache_key: str, max_age: int) -> Optional[Dict[str, Any]]:
    """Retrieve response from cache if valid."""
    cache_file = CACHE_DIR / f"{cache_key}.json"
    
    if not cache_file.exists():
        return None
    
    try:
        # Check if cache is expired
        file_age = time.time() - cache_file.stat().st_mtime
        if file_age > max_age:
            cache_file.unlink()  # Remove expired cache
            return None
        
        with open(cache_file, 'r') as f:
            return json.load(f)
    except Exception:
        return None


def _save_to_cache(cache_key: str, data: Dict[str, Any]) -> None:
    """Save response to cache."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"{cache_key}.json"
        with open(cache_file, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        # Silently fail on cache write errors
        pass


def _select_fields(data: Any, fields: str) -> Any:
    """Extract specified fields from data using dot notation.
    
    Examples:
        fields='title,user.handle' extracts just those fields
        Works with both dicts and lists of dicts
    """
    if not fields:
        return data
    
    field_list = [f.strip() for f in fields.split(',')]
    
    def extract_field(obj: Any, path: str) -> Any:
        """Extract a single field using dot notation."""
        parts = path.split('.')
        current = obj
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
            if current is None:
                return None
        return current
    
    def extract_fields(obj: Any) -> Dict[str, Any]:
        """Extract multiple fields from a single object."""
        result = {}
        for field in field_list:
            value = extract_field(obj, field)
            if value is not None:
                # Use the last part of the path as the key
                key = field.split('.')[-1]
                result[key] = value
        return result
    
    # Handle list of objects
    if isinstance(data, list):
        return [extract_fields(item) for item in data]
    # Handle single object
    elif isinstance(data, dict):
        return extract_fields(data)
    else:
        return data


def _request(ctx: Dict[str, Any], method: str, path: str, path_params: Dict[str, Any], query_params: Dict[str, Any], header_params: Optional[Dict[str, Any]] = None) -> None:
    """Perform an HTTP request and print the response."""
    base_url: str = ctx.get('base_url', DEFAULT_BASE_URL)
    format_output: str = ctx.get('format', 'pretty')
    output_file: Optional[str] = ctx.get('output')
    quiet_mode: bool = ctx.get('quiet', False)
    cache_ttl: Optional[int] = ctx.get('cache')
    select_fields: Optional[str] = ctx.get('select')
    
    for name, value in path_params.items():
        path = path.replace(f'{{{name}}}', str(value))
    url = base_url.rstrip('/') + path
    params: List[tuple] = []
    for name, value in query_params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                params.append((name, item))
        else:
            params.append((name, value))
    
    # Check cache for GET requests
    data = None
    if method == 'GET' and cache_ttl and cache_ttl > 0:
        cache_key = _get_cache_key(url, params)
        data = _get_from_cache(cache_key, cache_ttl)
        if data is not None and not quiet_mode:
            click.echo(click.style("[From cache]", fg="yellow"), err=True)
    
    # Make request if not cached
    if data is None:
        try:
            resp = requests.request(method=method, url=url, params=params, headers=header_params)
        except requests.exceptions.RequestException as exc:
            # Improved error message
            click.echo(f"Network error: {exc}", err=True)
            click.echo("Hint: Check your internet connection and try again.", err=True)
            sys.exit(1)
    
        if resp.status_code >= 400:
            # Improved error messages with hints
            error_msg = f"Request failed with status {resp.status_code}"
            if resp.status_code == 404:
                error_msg += "\nHint: Resource not found. Check the ID or URL path."
            elif resp.status_code == 429:
                error_msg += "\nHint: Rate limit exceeded. Please wait a moment before trying again."
            elif resp.status_code >= 500:
                error_msg += "\nHint: Server error. The Audius API may be experiencing issues. Try again later."
            click.echo(f"{error_msg}\nResponse: {resp.text}", err=True)
            sys.exit(resp.status_code)
        
        try:
            data = resp.json()
            
            # Save to cache if enabled
            if method == 'GET' and cache_ttl and cache_ttl > 0:
                cache_key = _get_cache_key(url, params)
                _save_to_cache(cache_key, data)
        except ValueError:
            click.echo(resp.text)
            return
    
    # Apply field selection before extracting data array
    if select_fields:
        # If response has a 'data' key, apply selection to that
        if isinstance(data, dict) and 'data' in data:
            data['data'] = _select_fields(data['data'], select_fields)
        else:
            data = _select_fields(data, select_fields)
    
    # Extract just the data array if quiet mode
    if quiet_mode and isinstance(data, dict) and 'data' in data:
        data = data['data']
    
    # Format output
    if format_output == 'pretty':
        output = json.dumps(data, indent=2, ensure_ascii=False)
    elif format_output == 'compact':
        output = json.dumps(data, ensure_ascii=False)
    else:  # raw
        output = str(data)
    
    # Write to file or stdout
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(output)
        if not quiet_mode:
            click.echo(click.style(f"Response saved to {output_file}", fg="green"))
    else:
        if not quiet_mode:
            click.echo(click.style("JSON response:", fg="green"))
        click.echo(output)


def _graphql_request(ctx: Dict[str, Any], query: str, variables: Optional[Dict[str, Any]] = None) -> None:
    """Perform a GraphQL request and print the response."""
    api_key: Optional[str] = ctx.get('graphql_api_key')
    if not api_key:
        click.echo("Error: GraphQL API key required.", err=True)
        click.echo("\nHow to get an API key:", err=True)
        click.echo("1. Visit https://thegraph.com/studio/apikeys/", err=True)
        click.echo("2. Create a free account and generate an API key", err=True)
        click.echo("3. Set it via:", err=True)
        click.echo("   - Environment variable: export AUDIUS_GRAPHQL_KEY=your-key", err=True)
        click.echo("   - CLI flag: --graphql-api-key your-key", err=True)
        click.echo("   - Config file: ~/.audius-cli/config.yaml", err=True)
        sys.exit(1)
    
    format_output: str = ctx.get('format', 'pretty')
    output_file: Optional[str] = ctx.get('output')
    quiet_mode: bool = ctx.get('quiet', False)
    
    # Build endpoint URL with API key
    endpoint = DEFAULT_GRAPHQL_ENDPOINT.format(api_key=api_key)
    
    # Prepare GraphQL request body
    payload = {'query': query}
    if variables:
        payload['variables'] = variables
    
    try:
        resp = requests.post(endpoint, json=payload, headers={'Content-Type': 'application/json'})
    except requests.exceptions.RequestException as exc:
        click.echo(f"Network error: {exc}", err=True)
        sys.exit(1)
    
    if resp.status_code >= 400:
        click.echo(f"Request failed with status {resp.status_code}: {resp.text}", err=True)
        sys.exit(resp.status_code)
    
    try:
        result = resp.json()
        
        # Check for GraphQL errors
        if 'errors' in result:
            click.echo("GraphQL Errors:", err=True)
            for error in result['errors']:
                click.echo(f"  - {error.get('message', str(error))}", err=True)
            if 'data' not in result or result['data'] is None:
                sys.exit(1)
        
        # Extract data
        data = result.get('data', result)
        
        # Format output
        if format_output == 'pretty':
            output = json.dumps(data, indent=2, ensure_ascii=False)
        elif format_output == 'compact':
            output = json.dumps(data, ensure_ascii=False)
        else:  # raw
            output = str(data)
        
        # Write to file or stdout
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(output)
            if not quiet_mode:
                click.echo(click.style(f"Response saved to {output_file}", fg="green"))
        else:
            if not quiet_mode:
                click.echo(click.style("GraphQL response:", fg="cyan"))
            click.echo(output)
    except ValueError as e:
        click.echo(f"Invalid JSON response: {e}", err=True)
        click.echo(resp.text)
        sys.exit(1)


@click.group()
@click.option('--base-url', help='Base URL of the Audius API')
@click.option('--graphql-api-key', envvar='AUDIUS_GRAPHQL_KEY', help='The Graph API key for GraphQL queries (or set AUDIUS_GRAPHQL_KEY env var)')
@click.option('--format', type=click.Choice(['pretty', 'compact', 'raw']), help='Output format for JSON responses')
@click.option('--output', '-o', type=click.Path(), help='Write output to file instead of stdout')
@click.option('--quiet', '-q', is_flag=True, help='Quiet mode - only show data array without metadata')
@click.option('--cache', type=int, help='Cache responses for N seconds (e.g., --cache 300 for 5 minutes)')
@click.option('--select', '-s', help='Select specific fields from response (e.g., "title,user.handle,play_count")')
@click.pass_context
def cli(ctx: click.Context, base_url: Optional[str], graphql_api_key: Optional[str], format: Optional[str], output: Optional[str], quiet: bool, cache: Optional[int], select: Optional[str]) -> None:
    """Audius API command-line interface.
    
    Supports both REST API (content) and GraphQL (governance/staking) endpoints.
    Configuration can be set via ~/.audius-cli/config.yaml, environment variables, or CLI flags.
    Priority: CLI flags > environment variables > config file > defaults
    """
    # Load config file
    config = _load_config()
    
    # Build context with priority: CLI args > env vars > config > defaults
    ctx.obj = {
        'base_url': base_url or os.environ.get('AUDIUS_BASE_URL') or config.get('base_url') or DEFAULT_BASE_URL,
        'graphql_api_key': graphql_api_key or config.get('graphql_api_key'),
        'format': format or config.get('format') or 'pretty',
        'output': output or config.get('output'),
        'quiet': quiet or config.get('quiet', False),
        'cache': cache if cache is not None else config.get('cache'),
        'select': select or config.get('select')
    }



@cli.group(name='challenges')
def challenges() -> None:
    """Challenges related operations"""
    pass

@challenges.command(name='get_undisbursed_challenges')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="A User ID to filter the undisbursed challenges to a particular user")
@click.option('--completed-blocknumber', type=int, help="Starting blocknumber to retrieve completed undisbursed challenges")
@click.pass_obj
def get_undisbursed_challenges(ctx: Dict[str, Any], offset: Optional[str], limit: Optional[str], user_id: Optional[str], completed_blocknumber: Optional[str]) -> None:
    """Get all undisbursed challenges"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
        'completed_blocknumber': completed_blocknumber,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/challenges/undisbursed', path_vals, query, headers)



@cli.group(name='comments')
def comments() -> None:
    """Comments related operations"""
    pass

@comments.command(name='get_comment_replies')
@click.argument('comment_id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_comment_replies(ctx: Dict[str, Any], comment_id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str]) -> None:
    """Gets replies to a parent comment"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'comment_id': comment_id,
    }
    _request(ctx, 'GET', '/comments/{comment_id}/replies', path_vals, query, headers)

@comments.command(name='get_unclaimed_comment_id')
@click.pass_obj
def get_unclaimed_comment_id(ctx: Dict[str, Any]) -> None:
    """Gets an unclaimed blockchain comment ID"""
    query = {
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/comments/unclaimed_id', path_vals, query, headers)



@cli.group(name='dashboard_wallet_users')
def dashboard_wallet_users() -> None:
    """Dashboard wallet users related operations"""
    pass

@dashboard_wallet_users.command(name='bulk_get_dashboard_wallet_users')
@click.option('--wallets', multiple=True, help="The wallets for which to fetch connected Audius user profiles.")
@click.pass_obj
def bulk_get_dashboard_wallet_users(ctx: Dict[str, Any], wallets: Optional[str]) -> None:
    """Gets Audius user profiles connected to given dashboard wallet addresses"""
    query = {
        'wallets': list(wallets) if wallets else [],
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/dashboard_wallet_users', path_vals, query, headers)



@cli.group(name='developer_apps')
def developer_apps() -> None:
    """Developer apps related operations"""
    pass

@developer_apps.command(name='get_developer_app')
@click.argument('address')
@click.pass_obj
def get_developer_app(ctx: Dict[str, Any], address: Optional[str]) -> None:
    """Gets developer app matching given address (API key)"""
    query = {
    }
    headers = None
    path_vals = {
        'address': address,
    }
    _request(ctx, 'GET', '/developer_apps/{address}', path_vals, query, headers)



@cli.group(name='playlists')
def playlists() -> None:
    """Playlists related operations"""
    pass

@playlists.command(name='get_bulk_playlists')
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--id', multiple=True, help="The ID of the playlist(s)")
@click.pass_obj
def get_bulk_playlists(ctx: Dict[str, Any], user_id: Optional[str], id: Optional[str]) -> None:
    """Gets a list of playlists by ID"""
    query = {
        'user_id': user_id,
        'id': list(id) if id else [],
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/playlists', path_vals, query, headers)

@playlists.command(name='get_playlist')
@click.argument('playlist_id')
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_playlist(ctx: Dict[str, Any], playlist_id: Optional[str], user_id: Optional[str]) -> None:
    """Get a playlist by ID"""
    query = {
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'playlist_id': playlist_id,
    }
    _request(ctx, 'GET', '/playlists/{playlist_id}', path_vals, query, headers)

@playlists.command(name='get_playlist_access_info')
@click.argument('playlist_id')
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_playlist_access_info(ctx: Dict[str, Any], playlist_id: Optional[str], user_id: Optional[str]) -> None:
    """Gets the information necessary to access the playlist and what access the given user has."""
    query = {
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'playlist_id': playlist_id,
    }
    _request(ctx, 'GET', '/playlists/{playlist_id}/access-info', path_vals, query, headers)

@playlists.command(name='get_playlist_by_handle_and_slug')
@click.argument('handle')
@click.argument('slug')
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_playlist_by_handle_and_slug(ctx: Dict[str, Any], handle: Optional[str], slug: Optional[str], user_id: Optional[str]) -> None:
    """Get a playlist by handle and slug"""
    query = {
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'handle': handle,
        'slug': slug,
    }
    _request(ctx, 'GET', '/playlists/by_permalink/{handle}/{slug}', path_vals, query, headers)

@playlists.command(name='get_playlist_tracks')
@click.argument('playlist_id')
@click.pass_obj
def get_playlist_tracks(ctx: Dict[str, Any], playlist_id: Optional[str]) -> None:
    """Fetch tracks within a playlist."""
    query = {
    }
    headers = None
    path_vals = {
        'playlist_id': playlist_id,
    }
    _request(ctx, 'GET', '/playlists/{playlist_id}/tracks', path_vals, query, headers)

@playlists.command(name='get_trending_playlists')
@click.option('--time', help="Calculate trending over a specified time range")
@click.pass_obj
def get_trending_playlists(ctx: Dict[str, Any], time: Optional[str]) -> None:
    """Gets trending playlists for a time period"""
    query = {
        'time': time,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/playlists/trending', path_vals, query, headers)

@playlists.command(name='search_playlists')
@click.option('--query', help="The search query")
@click.option('--genre', multiple=True, help="The genres to filter by")
@click.option('--sort-method', help="The sort method")
@click.option('--mood', multiple=True, help="The moods to filter by")
@click.option('--includePurchaseable', help="Whether or not to include purchaseable content")
@click.option('--has-downloads', help="Only include tracks that have downloads in the track results")
@click.pass_obj
def search_playlists(ctx: Dict[str, Any], query: Optional[str], genre: Optional[str], sort_method: Optional[str], mood: Optional[str], includePurchaseable: Optional[str], has_downloads: Optional[str]) -> None:
    """Search for a playlist"""
    query = {
        'query': query,
        'genre': list(genre) if genre else [],
        'sort_method': sort_method,
        'mood': list(mood) if mood else [],
        'includePurchaseable': includePurchaseable,
        'has_downloads': has_downloads,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/playlists/search', path_vals, query, headers)



@cli.group(name='resolve')
def resolve() -> None:
    """Resolve related operations"""
    pass

@resolve.command(name='resolve')
@click.option('--url', help="URL to resolve. Either fully formed URL (https://audius.co) or just the absolute path")
@click.pass_obj
def resolve(ctx: Dict[str, Any], url: Optional[str]) -> None:
    """This endpoint allows you to lookup and access API resources when you only know the audius.co URL. Tracks, Playlists, and Users are supported."""
    query = {
        'url': url,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/resolve', path_vals, query, headers)



@cli.group(name='tips')
def tips() -> None:
    """Tips related operations"""
    pass

@tips.command(name='get_tips')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--receiver-min-followers', type=int, help="Only include tips to recipients that have this many followers")
@click.option('--receiver-is-verified', is_flag=True, help="Only include tips to recipients that are verified")
@click.option('--current-user-follows', help="Only include tips involving the user's followers in the given capacity. Requires user_id to be set.")
@click.option('--unique-by', help="Only include the most recent tip for a user was involved in the given capacity.  Eg. 'sender' will ensure that each tip returned has a unique sender, using the most recent tip sent by a user if that user has sent multiple tips.     ")
@click.pass_obj
def get_tips(ctx: Dict[str, Any], offset: Optional[str], limit: Optional[str], user_id: Optional[str], receiver_min_followers: Optional[str], receiver_is_verified: Optional[str], current_user_follows: Optional[str], unique_by: Optional[str]) -> None:
    """Gets the most recent tips on the network"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
        'receiver_min_followers': receiver_min_followers,
        'receiver_is_verified': receiver_is_verified if receiver_is_verified else None,
        'current_user_follows': current_user_follows,
        'unique_by': unique_by,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/tips', path_vals, query, headers)



@cli.group(name='tracks')
def tracks() -> None:
    """Tracks related operations"""
    pass

@tracks.command(name='download_track')
@click.argument('track_id')
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--user-signature', help="Optional - signature from the requesting user's wallet.         This is needed to authenticate the user and verify access in case the track is gated.")
@click.option('--user-data', help="Optional - data which was used to generate the optional signature argument.")
@click.option('--nft-access-signature', help="Optional - nft access signature for this track which was previously generated by a registered DN.         We perform checks on it and pass it through to CN.")
@click.option('--original', is_flag=True, help="Optional - true if downloading original file")
@click.option('--filename', help="Optional - name of file to download. If not provided, defaults to track original filename or title.")
@click.pass_obj
def download_track(ctx: Dict[str, Any], track_id: Optional[str], user_id: Optional[str], user_signature: Optional[str], user_data: Optional[str], nft_access_signature: Optional[str], original: Optional[str], filename: Optional[str]) -> None:
    """Download an original or mp3 track"""
    query = {
        'user_id': user_id,
        'user_signature': user_signature,
        'user_data': user_data,
        'nft_access_signature': nft_access_signature,
        'original': original if original else None,
        'filename': filename,
    }
    headers = None
    path_vals = {
        'track_id': track_id,
    }
    _request(ctx, 'GET', '/tracks/{track_id}/download', path_vals, query, headers)

@tracks.command(name='get_bulk_tracks')
@click.option('--permalink', multiple=True, help="The permalink of the track(s)")
@click.option('--id', multiple=True, help="The ID of the track(s)")
@click.pass_obj
def get_bulk_tracks(ctx: Dict[str, Any], permalink: Optional[str], id: Optional[str]) -> None:
    """Gets a list of tracks using their IDs or permalinks"""
    query = {
        'permalink': list(permalink) if permalink else [],
        'id': list(id) if id else [],
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/tracks', path_vals, query, headers)

@tracks.command(name='get_track')
@click.argument('track_id')
@click.pass_obj
def get_track(ctx: Dict[str, Any], track_id: Optional[str]) -> None:
    """Gets a track by ID"""
    query = {
    }
    headers = None
    path_vals = {
        'track_id': track_id,
    }
    _request(ctx, 'GET', '/tracks/{track_id}', path_vals, query, headers)

@tracks.command(name='get_track_access_info')
@click.argument('track_id')
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_track_access_info(ctx: Dict[str, Any], track_id: Optional[str], user_id: Optional[str]) -> None:
    """Gets the information necessary to access the track and what access the given user has."""
    query = {
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'track_id': track_id,
    }
    _request(ctx, 'GET', '/tracks/{track_id}/access-info', path_vals, query, headers)

@tracks.command(name='get_track_stems')
@click.argument('track_id')
@click.pass_obj
def get_track_stems(ctx: Dict[str, Any], track_id: Optional[str]) -> None:
    """Get the remixable stems of a track"""
    query = {
    }
    headers = None
    path_vals = {
        'track_id': track_id,
    }
    _request(ctx, 'GET', '/tracks/{track_id}/stems', path_vals, query, headers)

@tracks.command(name='get_track_top_listeners')
@click.argument('track_id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_track_top_listeners(ctx: Dict[str, Any], track_id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str]) -> None:
    """Get the users that have listened to a track the most"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'track_id': track_id,
    }
    _request(ctx, 'GET', '/tracks/{track_id}/top_listeners', path_vals, query, headers)

@tracks.command(name='get_trending_tracks')
@click.option('--genre', help="Filter trending to a specified genre")
@click.option('--time', help="Calculate trending over a specified time range")
@click.pass_obj
def get_trending_tracks(ctx: Dict[str, Any], genre: Optional[str], time: Optional[str]) -> None:
    """Gets the top 100 trending (most popular) tracks on Audius"""
    query = {
        'genre': genre,
        'time': time,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/tracks/trending', path_vals, query, headers)

@tracks.command(name='get_underground_trending_tracks')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.pass_obj
def get_underground_trending_tracks(ctx: Dict[str, Any], offset: Optional[str], limit: Optional[str]) -> None:
    """Gets the top 100 trending underground tracks on Audius"""
    query = {
        'offset': offset,
        'limit': limit,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/tracks/trending/underground', path_vals, query, headers)

@tracks.command(name='inspect_track')
@click.argument('track_id')
@click.option('--original', is_flag=True, help="Optional - if set to true inspects the original quality file")
@click.pass_obj
def inspect_track(ctx: Dict[str, Any], track_id: Optional[str], original: Optional[str]) -> None:
    """Inspect a track"""
    query = {
        'original': original if original else None,
    }
    headers = None
    path_vals = {
        'track_id': track_id,
    }
    _request(ctx, 'GET', '/tracks/{track_id}/inspect', path_vals, query, headers)

@tracks.command(name='search_tracks')
@click.option('--query', help="The search query")
@click.option('--genre', multiple=True, help="The genres to filter by")
@click.option('--sort-method', help="The sort method")
@click.option('--mood', multiple=True, help="The moods to filter by")
@click.option('--only-downloadable', help="Return only downloadable tracks")
@click.option('--includePurchaseable', help="Whether or not to include purchaseable content")
@click.option('--is-purchaseable', help="Only include purchaseable tracks and albums in the track and album results")
@click.option('--has-downloads', help="Only include tracks that have downloads in the track results")
@click.option('--key', multiple=True, help="Only include tracks that match the musical key")
@click.option('--bpm-min', help="Only include tracks that have a bpm greater than or equal to")
@click.option('--bpm-max', help="Only include tracks that have a bpm less than or equal to")
@click.pass_obj
def search_tracks(ctx: Dict[str, Any], query: Optional[str], genre: Optional[str], sort_method: Optional[str], mood: Optional[str], only_downloadable: Optional[str], includePurchaseable: Optional[str], is_purchaseable: Optional[str], has_downloads: Optional[str], key: Optional[str], bpm_min: Optional[str], bpm_max: Optional[str]) -> None:
    """Search for a track or tracks"""
    query = {
        'query': query,
        'genre': list(genre) if genre else [],
        'sort_method': sort_method,
        'mood': list(mood) if mood else [],
        'only_downloadable': only_downloadable,
        'includePurchaseable': includePurchaseable,
        'is_purchaseable': is_purchaseable,
        'has_downloads': has_downloads,
        'key': list(key) if key else [],
        'bpm_min': bpm_min,
        'bpm_max': bpm_max,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/tracks/search', path_vals, query, headers)

@tracks.command(name='stream_track')
@click.argument('track_id')
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--preview', is_flag=True, help="Optional - true if streaming track preview")
@click.option('--user-signature', help="Optional - signature from the requesting user's wallet.         This is needed to authenticate the user and verify access in case the track is gated.")
@click.option('--user-data', help="Optional - data which was used to generate the optional signature argument.")
@click.option('--nft-access-signature', help="Optional - gated content signature for this track which was previously generated by a registered DN.         We perform checks on it and pass it through to CN.")
@click.option('--skip-play-count', is_flag=True, help="Optional - boolean that disables tracking of play counts.")
@click.option('--api-key', help="Optional - API key for third party apps. This is required for tracks that only allow specific API keys.")
@click.option('--skip-check', is_flag=True, help="Optional - POC to skip node 'double dip' health check")
@click.option('--no-redirect', is_flag=True, help="Optional - If true will not return a 302 and instead will return the stream url in JSON")
@click.pass_obj
def stream_track(ctx: Dict[str, Any], track_id: Optional[str], user_id: Optional[str], preview: Optional[str], user_signature: Optional[str], user_data: Optional[str], nft_access_signature: Optional[str], skip_play_count: Optional[str], api_key: Optional[str], skip_check: Optional[str], no_redirect: Optional[str]) -> None:
    """Stream an mp3 track This endpoint accepts the Range header for streaming. https://developer.mozilla.org/en-US/docs/Web/HTTP/Range_requests"""
    query = {
        'user_id': user_id,
        'preview': preview if preview else None,
        'user_signature': user_signature,
        'user_data': user_data,
        'nft_access_signature': nft_access_signature,
        'skip_play_count': skip_play_count if skip_play_count else None,
        'api_key': api_key,
        'skip_check': skip_check if skip_check else None,
        'no_redirect': no_redirect if no_redirect else None,
    }
    headers = None
    path_vals = {
        'track_id': track_id,
    }
    _request(ctx, 'GET', '/tracks/{track_id}/stream', path_vals, query, headers)

@tracks.command(name='track_comment_count')
@click.argument('track_id')
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def track_comment_count(ctx: Dict[str, Any], track_id: Optional[str], user_id: Optional[str]) -> None:
    """Get the comment count for a track"""
    query = {
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'track_id': track_id,
    }
    _request(ctx, 'GET', '/tracks/{track_id}/comment_count', path_vals, query, headers)

@tracks.command(name='track_comment_notification_setting')
@click.argument('track_id')
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def track_comment_notification_setting(ctx: Dict[str, Any], track_id: Optional[str], user_id: Optional[str]) -> None:
    """Get the comment notification setting for a track"""
    query = {
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'track_id': track_id,
    }
    _request(ctx, 'GET', '/tracks/{track_id}/comment_notification_setting', path_vals, query, headers)

@tracks.command(name='track_comments')
@click.argument('track_id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--sort-method', help="The sort method")
@click.pass_obj
def track_comments(ctx: Dict[str, Any], track_id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str], sort_method: Optional[str]) -> None:
    """Get a list of comments for a track"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
        'sort_method': sort_method,
    }
    headers = None
    path_vals = {
        'track_id': track_id,
    }
    _request(ctx, 'GET', '/tracks/{track_id}/comments', path_vals, query, headers)



@cli.group(name='users')
def users() -> None:
    """Users related operations"""
    pass

@users.command(name='download_purchases_as_csv')
@click.argument('id')
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--encoded-data-message', help="The data that was signed by the user for signature recovery")
@click.option('--encoded-data-signature', help="The signature of data, used for signature recovery")
@click.pass_obj
def download_purchases_as_csv(ctx: Dict[str, Any], id: Optional[str], user_id: Optional[str], encoded_data_message: Optional[str], encoded_data_signature: Optional[str]) -> None:
    """Downloads the purchases the user has made as a CSV file"""
    query = {
        'user_id': user_id,
    }
    headers = {
        'Encoded-Data-Message': encoded_data_message,
        'Encoded-Data-Signature': encoded_data_signature,
    }
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/purchases/download', path_vals, query, headers)

@users.command(name='download_sales_as_csv')
@click.argument('id')
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--encoded-data-message', help="The data that was signed by the user for signature recovery")
@click.option('--encoded-data-signature', help="The signature of data, used for signature recovery")
@click.pass_obj
def download_sales_as_csv(ctx: Dict[str, Any], id: Optional[str], user_id: Optional[str], encoded_data_message: Optional[str], encoded_data_signature: Optional[str]) -> None:
    """Downloads the sales the user has made as a CSV file"""
    query = {
        'user_id': user_id,
    }
    headers = {
        'Encoded-Data-Message': encoded_data_message,
        'Encoded-Data-Signature': encoded_data_signature,
    }
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/sales/download', path_vals, query, headers)

@users.command(name='download_usdc_withdrawals_as_csv')
@click.argument('id')
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--encoded-data-message', help="The data that was signed by the user for signature recovery")
@click.option('--encoded-data-signature', help="The signature of data, used for signature recovery")
@click.pass_obj
def download_usdc_withdrawals_as_csv(ctx: Dict[str, Any], id: Optional[str], user_id: Optional[str], encoded_data_message: Optional[str], encoded_data_signature: Optional[str]) -> None:
    """Downloads the USDC withdrawals the user has made as a CSV file"""
    query = {
        'user_id': user_id,
    }
    headers = {
        'Encoded-Data-Message': encoded_data_message,
        'Encoded-Data-Signature': encoded_data_signature,
    }
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/withdrawals/download', path_vals, query, headers)

@users.command(name='get_ai_attributed_tracks_by_user_handle')
@click.argument('handle')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--sort', help="[Deprecated] Field to sort by")
@click.option('--query', help="The filter query")
@click.option('--sort-method', help="The sort method")
@click.option('--sort-direction', help="The sort direction")
@click.option('--filter-tracks', help="Filter by unlisted or public tracks")
@click.option('--encoded-data-message', help="The data that was signed by the user for signature recovery")
@click.option('--encoded-data-signature', help="The signature of data, used for signature recovery")
@click.pass_obj
def get_ai_attributed_tracks_by_user_handle(ctx: Dict[str, Any], handle: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str], sort: Optional[str], query: Optional[str], sort_method: Optional[str], sort_direction: Optional[str], filter_tracks: Optional[str], encoded_data_message: Optional[str], encoded_data_signature: Optional[str]) -> None:
    """Gets the AI generated tracks attributed to a user using the user's handle"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
        'sort': sort,
        'query': query,
        'sort_method': sort_method,
        'sort_direction': sort_direction,
        'filter_tracks': filter_tracks,
    }
    headers = {
        'Encoded-Data-Message': encoded_data_message,
        'Encoded-Data-Signature': encoded_data_signature,
    }
    path_vals = {
        'handle': handle,
    }
    _request(ctx, 'GET', '/users/handle/{handle}/tracks/ai_attributed', path_vals, query, headers)

@users.command(name='get_authorized_apps')
@click.argument('id')
@click.pass_obj
def get_authorized_apps(ctx: Dict[str, Any], id: Optional[str]) -> None:
    """Get the apps that user has authorized to write to their account"""
    query = {
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/authorized_apps', path_vals, query, headers)

@users.command(name='get_bulk_users')
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--id', multiple=True, help="The ID of the user(s)")
@click.pass_obj
def get_bulk_users(ctx: Dict[str, Any], user_id: Optional[str], id: Optional[str]) -> None:
    """Gets a list of users by ID"""
    query = {
        'user_id': user_id,
        'id': list(id) if id else [],
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/users', path_vals, query, headers)

@users.command(name='get_developer_apps')
@click.argument('id')
@click.pass_obj
def get_developer_apps(ctx: Dict[str, Any], id: Optional[str]) -> None:
    """Gets the developer apps that the user owns"""
    query = {
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/developer_apps', path_vals, query, headers)

@users.command(name='get_favorites')
@click.argument('id')
@click.pass_obj
def get_favorites(ctx: Dict[str, Any], id: Optional[str]) -> None:
    """Gets a user's favorite tracks"""
    query = {
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/favorites', path_vals, query, headers)

@users.command(name='get_followers')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_followers(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str]) -> None:
    """All users that follow the provided user"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/followers', path_vals, query, headers)

@users.command(name='get_following')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_following(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str]) -> None:
    """All users that the provided user follows"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/following', path_vals, query, headers)

@users.command(name='get_muted_users')
@click.argument('id')
@click.option('--encoded-data-message', help="The data that was signed by the user for signature recovery")
@click.option('--encoded-data-signature', help="The signature of data, used for signature recovery")
@click.pass_obj
def get_muted_users(ctx: Dict[str, Any], id: Optional[str], encoded_data_message: Optional[str], encoded_data_signature: Optional[str]) -> None:
    """Gets users muted by the given user"""
    query = {
    }
    headers = {
        'Encoded-Data-Message': encoded_data_message,
        'Encoded-Data-Signature': encoded_data_signature,
    }
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/muted', path_vals, query, headers)

@users.command(name='get_related_users')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_related_users(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str]) -> None:
    """Gets a list of users that might be of interest to followers of this user."""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/related', path_vals, query, headers)

@users.command(name='get_reposts')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_reposts(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str]) -> None:
    """Gets the given user's reposts"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/reposts', path_vals, query, headers)

@users.command(name='get_sales_aggregate')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--encoded-data-message', help="The data that was signed by the user for signature recovery")
@click.option('--encoded-data-signature', help="The signature of data, used for signature recovery")
@click.pass_obj
def get_sales_aggregate(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str], encoded_data_message: Optional[str], encoded_data_signature: Optional[str]) -> None:
    """Gets the aggregated sales data for the user"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
    }
    headers = {
        'Encoded-Data-Message': encoded_data_message,
        'Encoded-Data-Signature': encoded_data_signature,
    }
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/sales/aggregate', path_vals, query, headers)

@users.command(name='get_subscribers')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_subscribers(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str]) -> None:
    """All users that subscribe to the provided user"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/subscribers', path_vals, query, headers)

@users.command(name='get_supported_users')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.pass_obj
def get_supported_users(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str]) -> None:
    """Gets the users that the given user supports"""
    query = {
        'offset': offset,
        'limit': limit,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/supporting', path_vals, query, headers)

@users.command(name='get_supporters')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.pass_obj
def get_supporters(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str]) -> None:
    """Gets the supporters of the given user"""
    query = {
        'offset': offset,
        'limit': limit,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/supporters', path_vals, query, headers)

@users.command(name='get_top_track_tags')
@click.argument('id')
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_top_track_tags(ctx: Dict[str, Any], id: Optional[str], limit: Optional[str], user_id: Optional[str]) -> None:
    """Gets the most used track tags by a user."""
    query = {
        'limit': limit,
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/tags', path_vals, query, headers)

@users.command(name='get_tracks_by_user')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--sort', help="[Deprecated] Field to sort by")
@click.option('--query', help="The filter query")
@click.option('--sort-method', help="The sort method")
@click.option('--sort-direction', help="The sort direction")
@click.option('--filter-tracks', help="Filter by unlisted or public tracks")
@click.option('--encoded-data-message', help="The data that was signed by the user for signature recovery")
@click.option('--encoded-data-signature', help="The signature of data, used for signature recovery")
@click.pass_obj
def get_tracks_by_user(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str], sort: Optional[str], query: Optional[str], sort_method: Optional[str], sort_direction: Optional[str], filter_tracks: Optional[str], encoded_data_message: Optional[str], encoded_data_signature: Optional[str]) -> None:
    """Gets the tracks created by a user using their user ID"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
        'sort': sort,
        'query': query,
        'sort_method': sort_method,
        'sort_direction': sort_direction,
        'filter_tracks': filter_tracks,
    }
    headers = {
        'Encoded-Data-Message': encoded_data_message,
        'Encoded-Data-Signature': encoded_data_signature,
    }
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/tracks', path_vals, query, headers)

@users.command(name='get_user')
@click.argument('id')
@click.pass_obj
def get_user(ctx: Dict[str, Any], id: Optional[str]) -> None:
    """Gets a single user by their user ID"""
    query = {
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}', path_vals, query, headers)

@users.command(name='get_user_challenges')
@click.argument('id')
@click.option('--show-historical', is_flag=True, help="Whether to show challenges that are inactive but completed")
@click.pass_obj
def get_user_challenges(ctx: Dict[str, Any], id: Optional[str], show_historical: Optional[str]) -> None:
    """Gets all challenges for the given user"""
    query = {
        'show_historical': show_historical if show_historical else None,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/challenges', path_vals, query, headers)

@users.command(name='get_user_id_from_wallet')
@click.option('--associated-wallet', help="Wallet address")
@click.pass_obj
def get_user_id_from_wallet(ctx: Dict[str, Any], associated_wallet: Optional[str]) -> None:
    """Gets a User ID from an associated wallet address"""
    query = {
        'associated_wallet': associated_wallet,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/users/id', path_vals, query, headers)

@users.command(name='get_user_tracks_remixed')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_user_tracks_remixed(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str]) -> None:
    """Gets tracks owned by the user which have been remixed by another track"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/tracks/remixed', path_vals, query, headers)

@users.command(name='get_user_by_handle')
@click.argument('handle')
@click.option('--user-id', help="The user ID of the user making the request")
@click.pass_obj
def get_user_by_handle(ctx: Dict[str, Any], handle: Optional[str], user_id: Optional[str]) -> None:
    """Gets a single user by their handle"""
    query = {
        'user_id': user_id,
    }
    headers = None
    path_vals = {
        'handle': handle,
    }
    _request(ctx, 'GET', '/users/handle/{handle}', path_vals, query, headers)

@users.command(name='get_connected_wallets')
@click.argument('id')
@click.pass_obj
def get_connected_wallets(ctx: Dict[str, Any], id: Optional[str]) -> None:
    """Get the User's ERC and SPL connected wallets"""
    query = {
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/connected_wallets', path_vals, query, headers)

@users.command(name='get_purchasers')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--content-type', help="Type of content to filter by (track or album)")
@click.option('--content-id', help="Filters for users who have purchased the given track or album ID")
@click.pass_obj
def get_purchasers(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str], content_type: Optional[str], content_id: Optional[str]) -> None:
    """Gets the list of unique users who have purchased content by the given user"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
        'content_type': content_type,
        'content_id': content_id,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/purchasers', path_vals, query, headers)

@users.command(name='get_remixers')
@click.argument('id')
@click.option('--offset', type=int, help="The number of items to skip. Useful for pagination (page number * limit)")
@click.option('--limit', type=int, help="The number of items to fetch")
@click.option('--user-id', help="The user ID of the user making the request")
@click.option('--track-id', help="Filters for remixers who have remixed the given track ID")
@click.pass_obj
def get_remixers(ctx: Dict[str, Any], id: Optional[str], offset: Optional[str], limit: Optional[str], user_id: Optional[str], track_id: Optional[str]) -> None:
    """Gets the list of unique users who have remixed tracks by the given user, or a specific track by that user if provided"""
    query = {
        'offset': offset,
        'limit': limit,
        'user_id': user_id,
        'track_id': track_id,
    }
    headers = None
    path_vals = {
        'id': id,
    }
    _request(ctx, 'GET', '/users/{id}/remixers', path_vals, query, headers)

@users.command(name='search_users')
@click.option('--query', help="The search query")
@click.option('--genre', multiple=True, help="The genres to filter by")
@click.option('--sort-method', help="The sort method")
@click.option('--is-verified', help="Only include verified users in the user results")
@click.pass_obj
def search_users(ctx: Dict[str, Any], query: Optional[str], genre: Optional[str], sort_method: Optional[str], is_verified: Optional[str]) -> None:
    """Search for users that match the given query"""
    query = {
        'query': query,
        'genre': list(genre) if genre else [],
        'sort_method': sort_method,
        'is_verified': is_verified,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/users/search', path_vals, query, headers)

@users.command(name='verify_id_token')
@click.option('--token', help="JWT to verify")
@click.pass_obj
def verify_id_token(ctx: Dict[str, Any], token: Optional[str]) -> None:
    """Verify if the given jwt ID token was signed by the subject (user) in the payload"""
    query = {
        'token': token,
    }
    headers = None
    path_vals = {}
    _request(ctx, 'GET', '/users/verify_token', path_vals, query, headers)



@cli.group(name='graphql')
def graphql() -> None:
    """GraphQL subgraph queries for governance and staking data"""
    pass

@graphql.command(name='query')
@click.argument('graphql_query')
@click.option('--variables', '-v', help='GraphQL variables as JSON string')
@click.pass_obj
def raw_query(ctx: Dict[str, Any], graphql_query: str, variables: Optional[str]) -> None:
    """Execute a custom GraphQL query
    
    Example:
        python websets-cli.py graphql query '{ audiusNetwork { totalSupply } }'
    """
    vars_dict = None
    if variables:
        try:
            vars_dict = json.loads(variables)
        except json.JSONDecodeError as e:
            click.echo(f"Invalid JSON in variables: {e}", err=True)
            sys.exit(1)
    
    _graphql_request(ctx, graphql_query, vars_dict)

@graphql.command(name='network-stats')
@click.pass_obj
def network_stats(ctx: Dict[str, Any]) -> None:
    """Get Audius network statistics and metrics"""
    query = """
    {
      audiusNetwork {
        id
        totalSupply
        totalTokensStaked
        totalTokensDelegated
        totalTokensClaimable
        maxDelegators
        minDelegationAmount
        undelegateLockupDuration
      }
    }
    """
    _graphql_request(ctx, query)

@graphql.command(name='service-nodes')
@click.option('--type', 'node_type', help='Filter by service type (discovery-node or content-node)')
@click.option('--limit', type=int, default=10, help='Number of results to return')
@click.pass_obj
def service_nodes(ctx: Dict[str, Any], node_type: Optional[str], limit: int) -> None:
    """List service nodes (discovery and content nodes)"""
    where_clause = ""
    if node_type:
        where_clause = f'where: {{type: "{node_type}"}}'
    
    query = f"""
    {{
      serviceNodes(first: {limit}, {where_clause}, orderBy: createdAt, orderDirection: desc) {{
        id
        spId
        owner {{
          id
        }}
        type
        endpoint
        delegateOwnerWallet
        createdAt
        isRegistered
      }}
    }}
    """
    _graphql_request(ctx, query)

@graphql.command(name='proposals')
@click.option('--status', help='Filter by status (pending, active, executed, vetoed, etc.)')
@click.option('--limit', type=int, default=10, help='Number of results to return')
@click.pass_obj
def proposals(ctx: Dict[str, Any], status: Optional[str], limit: int) -> None:
    """List governance proposals"""
    where_clause = ""
    if status:
        where_clause = f'where: {{outcome: "{status}"}}'
    
    query = f"""
    {{
      proposals(first: {limit}, {where_clause}, orderBy: submittedBlockNumber, orderDirection: desc) {{
        id
        proposalId
        proposer {{
          id
        }}
        name
        description
        outcome
        voteMagnitudeYes
        voteMagnitudeNo
        numVotes
        submittedBlockNumber
        targetContractAddress
        functionSignature
      }}
    }}
    """
    _graphql_request(ctx, query)

@graphql.command(name='user')
@click.argument('user_id')
@click.pass_obj
def user(ctx: Dict[str, Any], user_id: str) -> None:
    """Get detailed information about a user (by ETH address)
    
    Example:
        python websets-cli.py graphql user 0x1234...
    """
    query = f"""
    {{
      user(id: "{user_id.lower()}") {{
        id
        balance
        totalClaimableAmount
        totalStakedFor
        totalDelegatedToServiceProviders
        minAccountStake
        deployerCut
        serviceProviders {{
          id
          spId
          type
          endpoint
        }}
        claimRounds(first: 5, orderBy: blockNumber, orderDirection: desc) {{
          id
          blockNumber
          fundingAmount
        }}
      }}
    }}
    """
    _graphql_request(ctx, query)

@graphql.command(name='delegates')
@click.option('--from-user', help='Filter delegates from this user address')
@click.option('--to-user', help='Filter delegates to this user address')
@click.option('--limit', type=int, default=10, help='Number of results to return')
@click.pass_obj
def delegates(ctx: Dict[str, Any], from_user: Optional[str], to_user: Optional[str], limit: int) -> None:
    """List delegation relationships"""
    where_clauses = []
    if from_user:
        where_clauses.append(f'fromUser: "{from_user.lower()}"')
    if to_user:
        where_clauses.append(f'toUser: "{to_user.lower()}"')
    
    where_clause = ""
    if where_clauses:
        where_clause = 'where: {{' + ", ".join(where_clauses) + '}}'
    
    query = f"""
    {{
      delegates(first: {limit}, {where_clause}, orderBy: amount, orderDirection: desc) {{
        id
        fromUser {{
          id
        }}
        toUser {{
          id
        }}
        amount
        claimableAmount
      }}
    }}
    """
    _graphql_request(ctx, query)

if __name__ == '__main__':
    cli()
