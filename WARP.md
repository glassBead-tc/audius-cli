# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

**Audius CLI** - A command-line interface for interacting with the Audius web3 streaming platform using both the REST API and GraphQL subgraph. The REST API provides access to content (tracks, playlists, users) while the GraphQL subgraph provides access to on-chain governance and staking data.

**Repository:** Currently named `audius-cli` but refers to the Audius CLI.
**Main file:** `audius-cli.py` (~1430 lines, single-file architecture)

**API Endpoints:**
- REST API: `https://discoveryprovider.audius.co/v1` (public, no auth)
- GraphQL: `https://gateway.thegraph.com/api/[key]/subgraphs/id/F8TjrYuTLohz64J8uuDke9htSR1aY9TGCuEjJVVjUJaD` (requires API key)

## Common Commands

### Running the CLI

```bash
# Display help and available command groups
python audius-cli.py --help

# Display help for a specific group (e.g., tracks)
python audius-cli.py tracks --help

# Display help for a specific command
python audius-cli.py tracks get_track --help

# Example: Get trending tracks
python audius-cli.py tracks get_trending_tracks

# Example: Get a user by handle
python audius-cli.py users get_user_by_handle YourHandle

# Override the default API base URL
python audius-cli.py --base-url https://custom.audius.host/v1 tracks get_trending_tracks
```

### GraphQL Commands

**Setup:**
1. Get API key from [The Graph Studio](https://thegraph.com/studio/apikeys/)
2. Set environment variable: `export AUDIUS_GRAPHQL_KEY=your-key-here`
3. Or pass via flag: `--graphql-api-key your-key-here`

```bash
# Network statistics
python audius-cli.py graphql network-stats

# List service nodes (discovery/content nodes)
python audius-cli.py graphql service-nodes --limit 10
python audius-cli.py graphql service-nodes --type discovery-node

# Governance proposals
python audius-cli.py graphql proposals --limit 5
python audius-cli.py graphql proposals --status executed

# User info by ETH address
python audius-cli.py graphql user 0x1234567890abcdef...

# Delegation relationships
python audius-cli.py graphql delegates --limit 20
python audius-cli.py graphql delegates --from-user 0x1234...

# Custom GraphQL query
python audius-cli.py graphql query '{ audiusNetwork { totalSupply totalTokensStaked } }'

# With variables
python audius-cli.py graphql query 'query ($id: ID!) { user(id: $id) { balance } }' --variables '{"id": "0x123..."}'
```

### Output Formatting

```bash
# Pretty JSON (default)
python audius-cli.py --format pretty tracks get_trending_tracks

# Compact JSON (single line)
python audius-cli.py --format compact tracks get_trending_tracks

# Raw output
python audius-cli.py --format raw tracks get_trending_tracks

# Save to file
python audius-cli.py --output tracks.json tracks get_trending_tracks

# Quiet mode (data array only, no metadata)
python audius-cli.py --quiet tracks get_trending_tracks
```

### Field Selection

Extract specific fields from responses using `--select` with comma-separated paths:

```bash
# Select specific fields (works with nested paths)
python audius-cli.py --select 'title,genre,play_count' tracks get_trending_tracks

# Nested fields with dot notation
python audius-cli.py --select 'title,user.handle,user.follower_count' tracks get_trending_tracks

# Combine with quiet mode for clean output
python audius-cli.py --quiet --select 'title,play_count' tracks get_trending_tracks
```

### Response Caching

Cache API responses to reduce API calls and improve performance:

```bash
# Cache for 5 minutes (300 seconds)
python audius-cli.py --cache 300 tracks get_trending_tracks

# Cache for 1 hour
python audius-cli.py --cache 3600 tracks get_trending_tracks

# Second call will use cache (shows "[From cache]" indicator)
python audius-cli.py --cache 300 tracks get_trending_tracks
```

**Cache location:** `~/.audius-cli/cache/`
**Note:** Cache is automatically expired after the specified time

### Configuration File

Create `~/.audius-cli/config.yaml` to set default options:

```yaml
# Audius CLI Configuration
base_url: https://discoveryprovider.audius.co/v1
graphql_api_key: your-api-key-here
format: pretty
cache: 300  # Cache for 5 minutes by default
select: "title,user.handle,play_count"  # Default field selection
```

**Priority:** CLI flags > environment variables > config file > defaults

See `config.yaml.example` for full configuration options.

### Development Setup

**Dependencies:**
- `click` - CLI framework
- `requests` - HTTP client

```bash
# Install dependencies from requirements.txt
pip install -r requirements.txt

# Or install manually
pip install click requests

# Run the CLI directly
python audius-cli.py --help
```

### Testing and Validation

```bash
# Quick validation - test CLI loads and shows help
python audius-cli.py --help

# Test a specific command group
python audius-cli.py users --help

# Test a real API call (read-only)
python audius-cli.py tracks get_trending_tracks --time week
```

## Architecture

### Single-File Click Application

The entire CLI is contained in `audius-cli.py` with the following structure:

1. **Imports and Constants** (lines 1-17)
   - `click` for CLI framework
   - `requests` for HTTP
   - `DEFAULT_BASE_URL = "https://discoveryprovider.audius.co/v1"`

2. **Core Request Handler** (lines 21-50)
   - `_request()` function - central HTTP request logic

3. **CLI Root Group** (lines 52-58)
   - Main `@click.group()` decorator
   - `--base-url` option for overriding default API endpoint

4. **Command Groups** (lines 60-1123)
   - `challenges` - Challenge-related operations
   - `comments` - Comment operations
   - `dashboard_wallet_users` - Wallet user operations
   - `developer_apps` - Developer app operations
   - `playlists` - Playlist operations
   - `resolve` - URL resolution
   - `tips` - Tipping operations
   - `tracks` - Track operations (largest group)
   - `users` - User operations (largest group)

5. **Entry Point** (lines 1124-1125)
   - `if __name__ == '__main__': cli()`

### Request Flow

All commands follow this pattern:

```python
@group.command(name='command_name')
@click.option('--param', help="Description")
@click.pass_obj
def command_function(ctx: Dict[str, Any], param: Optional[str]) -> None:
    """Command docstring"""
    query = {'param': param}          # Query parameters
    headers = None                    # Optional headers
    path_vals = {}                    # Path parameter substitutions
    _request(ctx, 'GET', '/path', path_vals, query, headers)
```

**Key aspects:**
1. All API calls use HTTP GET
2. Context object contains `base_url` from CLI option
3. Path parameters are substituted via string replacement
4. Query parameters with `None` values are filtered out
5. Multiple values (from `multiple=True` options) are converted to lists
6. Boolean flags are handled specially (only sent if True)

### The `_request()` Function

Located at lines 21-50, this is the heart of the CLI:

```python
def _request(ctx, method, path, path_params, query_params, header_params=None):
    # 1. Build URL: base_url + path with path_params substituted
    base_url = ctx.get('base_url', DEFAULT_BASE_URL)
    for name, value in path_params.items():
        path = path.replace(f'{{{name}}}', str(value))
    url = base_url.rstrip('/') + path
    
    # 2. Build query parameters (handle lists and filter None)
    params = []
    for name, value in query_params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                params.append((name, item))
        else:
            params.append((name, value))
    
    # 3. Execute request with error handling
    try:
        resp = requests.request(method=method, url=url, params=params, headers=header_params)
    except requests.exceptions.RequestException as exc:
        click.echo(f"Network error: {exc}", err=True)
        sys.exit(1)
    
    # 4. Handle HTTP errors
    if resp.status_code >= 400:
        click.echo(f"Request failed with status {resp.status_code}: {resp.text}", err=True)
        sys.exit(resp.status_code)
    
    # 5. Output JSON response
    try:
        data = resp.json()
        click.echo(click.style("JSON response:", fg="green"))
        click.echo(data)
    except ValueError:
        click.echo(resp.text)
```

**Error Handling:**
- Network errors → stderr message + exit code 1
- HTTP 4xx/5xx → stderr message with status and response text + exit with status code
- JSON parse errors → output raw response text

**Output Format:**
- Successful JSON responses are printed with a green "JSON response:" prefix
- Raw dict/list output (not pretty-printed JSON)
- No `--output` or `--format` flags available

### Parameter Handling Patterns

**Path Parameters:**
- Defined as `@click.argument('param_name')`
- Substituted into path via `f'{{{name}}}'` placeholder replacement
- Example: `/users/{id}` → `/users/12345`

**Query Parameters:**
- Defined as `@click.option('--param-name')`
- CLI uses kebab-case (`--user-id`), Python uses snake_case (`user_id`)
- `None` values are automatically filtered out
- Multiple values use `multiple=True` and are converted to lists

**Boolean Flags:**
- Use `is_flag=True` in Click option
- Only sent to API if True (filtered if False/None)
- Example: `@click.option('--original', is_flag=True)`

**Header Parameters:**
- Some commands accept headers (e.g., `Encoded-Data-Message`, `Encoded-Data-Signature`)
- Defined as regular options, assembled into `headers` dict
- Example: User CSV downloads require signature headers

## Configuration

### API Base URL

**Default:** `https://discoveryprovider.audius.co/v1`

**Override:** Use `--base-url` flag at the CLI root level:
```bash
python audius-cli.py --base-url https://custom.host/v1 tracks get_trending_tracks
```

**No environment variable support** - base URL must be specified via CLI flag.

### Authentication

The Audius API appears to be **public/unauthenticated** for read operations. Some operations accept optional parameters:
- `user-id` - Contextual user for personalized results
- `user-signature` / `user-data` - Wallet signature verification for gated content
- `api-key` - For specific third-party app access

No API key environment variable or global auth mechanism is implemented.

## Known Limitations

1. **All GET requests** - No POST/PUT/DELETE operations (read-only API)
2. **No output formatting options** - Raw dict output, not pretty JSON
3. **No pagination helpers** - Offset/limit must be specified manually
4. **No configuration file** - All options via CLI flags
5. **No shell completion** - Would need to be added manually
6. **Exit codes** - Non-zero on any error, equals HTTP status code for API errors

## OpenAPI Specification

The CLI is generated from `audius-openapi-spec.yaml`:

**Spec Details:**
- **File:** `audius-openapi-spec.yaml` (3386 lines, 89KB)
- **OpenAPI Version:** 3.0.1
- **API Version:** 1.0
- **Server:** `https://discoveryprovider.audius.co/v1`

**Tags/Groups:**
- `users` - User related operations
- `playlists` - Playlist related operations
- `tracks` - Track related operations
- `challenges` - Challenge related operations
- `tips` - Tip related operations
- `developer_apps` - Developer app related operations
- `dashboard_wallet_users` - Protocol dashboard wallet users
- `resolve` - Audius Canonical URL resolver
- `comments` - Comment related operations

**API Characteristics:**
- All endpoints use HTTP GET (read-only API)
- Responses return JSON with defined schemas in `components/schemas`
- Common response codes: 200 (success), 400 (bad request), 404 (not found), 500 (server error)
- Pagination via `offset` and `limit` query parameters

## Development Notes

### Adding New Commands

Since this is auto-generated from an OpenAPI spec, manual additions should be avoided. Instead:
1. Update the OpenAPI specification (`audius-openapi-spec.yaml`)
2. Regenerate the CLI file using the codegen tool
3. Maintain this WARP.md separately

**Note:** The codegen process is not yet documented. The `audius-cli.py` file appears to be generated using a custom script rather than standard OpenAPI generators.

### Code Style

The generated code follows consistent patterns:
- Type hints on all function signatures
- Docstrings from OpenAPI operation descriptions
- Consistent parameter naming (API names vs CLI names)
- Dict-based context passing through Click

---

## 🚧 STUBS AND MISSING INFORMATION

The following sections need more information to be complete:

### 1. **Dependency Management** ✅
- [x] `requirements.txt` present with click and requests
- [ ] Python version requirements unknown (recommend Python 3.7+)
- [ ] Consider adding dev dependencies (black, ruff, mypy, pytest)
- [ ] Consider upgrading to pyproject.toml for modern packaging

### 2. **OpenAPI Specification** ✅
- [x] OpenAPI spec file: `audius-openapi-spec.yaml` (3386 lines, 89KB)
- [x] OpenAPI version: 3.0.1
- [x] API title: "API" (Audius V1 API)
- [x] Default server: `https://discoveryprovider.audius.co/v1`
- [ ] Codegen tool used for generation unknown (likely custom script based on the code structure)
- [ ] Regeneration instructions needed - need to document the process used to generate `audius-cli.py` from the spec

### 3. **Installation & Distribution** 📦
- [ ] No package metadata (setup.py/pyproject.toml)
- [ ] Not installable via pip
- [ ] No console script entry point configured
- [ ] Would benefit from proper packaging to enable `audius-cli` command

### 4. **Testing** 🧪
- [ ] No test suite present
- [ ] No test framework configured
- [ ] Would benefit from integration tests against public API
- [ ] Mock/fixture strategy needed for offline testing

### 5. **Linting & Formatting** 🎨
- [ ] No linter configuration (.flake8, ruff.toml, .ruff.toml)
- [ ] No formatter configuration (black, isort)
- [ ] No type checker configuration (mypy.ini, pyproject.toml [tool.mypy])
- [ ] No pre-commit hooks configured

### 6. **CI/CD** 🔄
- [ ] No GitHub Actions or CI configuration
- [ ] No automated testing
- [ ] No automated linting
- [ ] No release automation

### 7. **Documentation** 📚
- [ ] README.md is minimal (only "TBD" sections)
- [ ] No usage examples
- [ ] No API endpoint documentation
- [ ] No contribution guidelines

### 8. **Command Examples** 💡
- [ ] Need example workflows for common use cases
- [ ] Need documentation of popular endpoints
- [ ] Need error handling examples
- [ ] Need authentication examples for gated content

---

## NEXT STEPS FOR COMPLETION

**Priority 1 - Essential:**
1. ~~Provide OpenAPI specification file~~ ✅ Complete - `audius-openapi-spec.yaml` is present
2. Document codegen process/tool (determine which tool generated `audius-cli.py`)
3. Create `requirements.txt` or `pyproject.toml`
4. Add proper README with installation and usage examples

**Priority 2 - Quality:**
1. Add pytest test suite
2. Configure ruff/black/mypy
3. Add pre-commit hooks
4. Create proper package structure (pyproject.toml)

**Priority 3 - Nice to Have:**
1. GitHub Actions CI
2. Shell completion scripts
3. Pretty JSON output option
4. Configuration file support
