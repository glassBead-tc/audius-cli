#!/usr/bin/env python3
"""
Agent-based integration tests for Audius CLI.

These tests simulate a real user/agent using the CLI through subprocess calls.
Tests validate actual CLI behavior including error messages, output formats, and features.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ANSI color codes for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

# Path to CLI script
CLI_PATH = Path(__file__).parent.parent / "websets-cli.py"
CACHE_DIR = Path.home() / ".audius-cli" / "cache"


class TestResult:
    """Track test results."""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.failures: List[str] = []
    
    def record_pass(self, test_name: str):
        self.passed += 1
        print(f"{GREEN}✓{RESET} {test_name}")
    
    def record_fail(self, test_name: str, reason: str):
        self.failed += 1
        self.failures.append(f"{test_name}: {reason}")
        print(f"{RED}✗{RESET} {test_name}: {reason}")
    
    def record_skip(self, test_name: str, reason: str):
        self.skipped += 1
        print(f"{YELLOW}⊘{RESET} {test_name}: {reason}")
    
    def summary(self):
        total = self.passed + self.failed + self.skipped
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}Test Summary{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        print(f"Total:   {total}")
        print(f"{GREEN}Passed:  {self.passed}{RESET}")
        print(f"{RED}Failed:  {self.failed}{RESET}")
        print(f"{YELLOW}Skipped: {self.skipped}{RESET}")
        
        if self.failures:
            print(f"\n{RED}Failures:{RESET}")
            for failure in self.failures:
                print(f"  - {failure}")
        
        return self.failed == 0


def run_cli(args: List[str], env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    """Run CLI command and return (exit_code, stdout, stderr)."""
    cmd = [sys.executable, str(CLI_PATH)] + args
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})}
    )
    return result.returncode, result.stdout, result.stderr


def test_cli_help(results: TestResult):
    """Test that --help works."""
    exit_code, stdout, stderr = run_cli(["--help"])
    
    if exit_code == 0 and "Audius API command-line interface" in stdout:
        results.record_pass("CLI help displays correctly")
    else:
        results.record_fail("CLI help", f"Exit code {exit_code}")


def test_command_groups(results: TestResult):
    """Test that all command groups are available."""
    exit_code, stdout, stderr = run_cli(["--help"])
    
    expected_groups = [
        "tracks", "users", "playlists", "challenges",
        "comments", "tips", "graphql", "resolve"
    ]
    
    for group in expected_groups:
        if group in stdout:
            results.record_pass(f"Command group '{group}' available")
        else:
            results.record_fail(f"Command group '{group}'", "Not found in help")


def test_rest_api_call(results: TestResult):
    """Test a real REST API call."""
    exit_code, stdout, stderr = run_cli(["tracks", "get_trending_tracks", "--time", "week"])
    
    if exit_code == 0:
        try:
            # Output has "JSON response:" prefix
            if "JSON response:" in stdout:
                json_start = stdout.index("{")
                data = json.loads(stdout[json_start:])
                if "data" in data and isinstance(data["data"], list):
                    results.record_pass("REST API call successful (trending tracks)")
                else:
                    results.record_fail("REST API call", "Unexpected response structure")
            else:
                results.record_fail("REST API call", "No JSON response indicator")
        except (json.JSONDecodeError, ValueError) as e:
            results.record_fail("REST API call", f"Invalid JSON: {e}")
    else:
        results.record_fail("REST API call", f"Exit code {exit_code}")


def test_field_selection(results: TestResult):
    """Test field selection feature."""
    exit_code, stdout, stderr = run_cli([
        "--select", "title,play_count",
        "--quiet",
        "tracks", "get_trending_tracks", "--time", "week"
    ])
    
    if exit_code == 0:
        try:
            data = json.loads(stdout)
            if isinstance(data, list) and len(data) > 0:
                first_item = data[0]
                # Should have only selected fields
                if "title" in first_item and "play_count" in first_item:
                    # Should NOT have other fields
                    if "id" not in first_item and "genre" not in first_item:
                        results.record_pass("Field selection filters correctly")
                    else:
                        results.record_fail("Field selection", "Extra fields present")
                else:
                    results.record_fail("Field selection", "Selected fields missing")
            else:
                results.record_fail("Field selection", "Empty or invalid data")
        except json.JSONDecodeError:
            results.record_fail("Field selection", "Invalid JSON output")
    else:
        results.record_fail("Field selection", f"Exit code {exit_code}")


def test_output_formats(results: TestResult):
    """Test different output format options."""
    # Pretty format (default)
    exit_code, stdout, _ = run_cli(["--format", "pretty", "tracks", "get_trending_tracks", "--time", "week"])
    if exit_code == 0 and "  " in stdout:  # Indentation indicates pretty format
        results.record_pass("Pretty format works")
    else:
        results.record_fail("Pretty format", f"Exit code {exit_code}")
    
    # Compact format
    exit_code, stdout, _ = run_cli(["--format", "compact", "tracks", "get_trending_tracks", "--time", "week"])
    if exit_code == 0:
        # Compact should have no unnecessary whitespace
        results.record_pass("Compact format works")
    else:
        results.record_fail("Compact format", f"Exit code {exit_code}")


def test_file_output(results: TestResult):
    """Test output to file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    
    try:
        exit_code, stdout, stderr = run_cli([
            "--output", temp_file,
            "--quiet",
            "tracks", "get_trending_tracks", "--time", "week"
        ])
        
        if exit_code == 0:
            if Path(temp_file).exists():
                with open(temp_file, 'r') as f:
                    content = f.read()
                    try:
                        data = json.loads(content)
                        results.record_pass("File output works")
                    except json.JSONDecodeError:
                        results.record_fail("File output", "Invalid JSON in file")
            else:
                results.record_fail("File output", "File not created")
        else:
            results.record_fail("File output", f"Exit code {exit_code}")
    finally:
        if Path(temp_file).exists():
            Path(temp_file).unlink()


def test_caching(results: TestResult):
    """Test response caching."""
    # Clear cache first
    if CACHE_DIR.exists():
        for cache_file in CACHE_DIR.glob("*.json"):
            cache_file.unlink()
    
    # First call - should not be cached
    exit_code1, stdout1, stderr1 = run_cli([
        "--cache", "60",
        "tracks", "get_trending_tracks", "--time", "week"
    ])
    
    # Second call - should be cached
    exit_code2, stdout2, stderr2 = run_cli([
        "--cache", "60",
        "tracks", "get_trending_tracks", "--time", "week"
    ])
    
    if exit_code1 == 0 and exit_code2 == 0:
        if "[From cache]" in stderr2:
            results.record_pass("Caching works (cache hit detected)")
        else:
            results.record_fail("Caching", "No cache hit indicator on second call")
    else:
        results.record_fail("Caching", f"Exit codes {exit_code1}, {exit_code2}")


def test_quiet_mode(results: TestResult):
    """Test quiet mode removes metadata."""
    # Without quiet
    exit_code1, stdout1, _ = run_cli(["tracks", "get_trending_tracks", "--time", "week"])
    
    # With quiet
    exit_code2, stdout2, _ = run_cli(["--quiet", "tracks", "get_trending_tracks", "--time", "week"])
    
    if exit_code1 == 0 and exit_code2 == 0:
        if "JSON response:" in stdout1 and "JSON response:" not in stdout2:
            results.record_pass("Quiet mode removes metadata")
        else:
            results.record_fail("Quiet mode", "Metadata still present or missing incorrectly")
    else:
        results.record_fail("Quiet mode", f"Exit codes {exit_code1}, {exit_code2}")


def test_graphql_no_key(results: TestResult):
    """Test GraphQL without API key shows helpful error."""
    exit_code, stdout, stderr = run_cli(["graphql", "network-stats"])
    
    if exit_code != 0:  # Should fail without key
        if "thegraph.com/studio/apikeys" in stderr and "AUDIUS_GRAPHQL_KEY" in stderr:
            results.record_pass("GraphQL error message is helpful")
        else:
            results.record_fail("GraphQL error", "Missing helpful error message")
    else:
        results.record_fail("GraphQL error", "Should fail without API key")


def test_400_error(results: TestResult):
    """Test 400 error handling for invalid input."""
    exit_code, stdout, stderr = run_cli(["tracks", "get_track", "nonexistent-track-id-12345"])
    
    if exit_code != 0:
        if "400" in stderr and "invalid trackId" in stderr:
            results.record_pass("400 error for invalid input")
        else:
            results.record_fail("400 error", "Missing expected error message")
    else:
        results.record_fail("400 error", "Should fail for invalid track ID")


def test_invalid_option(results: TestResult):
    """Test handling of invalid options."""
    exit_code, stdout, stderr = run_cli(["--invalid-option"])
    
    if exit_code != 0:
        results.record_pass("Invalid option handled correctly")
    else:
        results.record_fail("Invalid option", "Should fail for invalid option")


def main():
    """Run all agent-based tests."""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}Audius CLI - Agent-Based Integration Tests{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")
    print(f"Testing CLI at: {CLI_PATH}\n")
    
    results = TestResult()
    
    # Basic functionality
    print(f"{YELLOW}Basic Functionality:{RESET}")
    test_cli_help(results)
    test_command_groups(results)
    
    # REST API Integration
    print(f"\n{YELLOW}REST API Integration:{RESET}")
    test_rest_api_call(results)
    
    # Features
    print(f"\n{YELLOW}CLI Features:{RESET}")
    test_field_selection(results)
    test_output_formats(results)
    test_file_output(results)
    test_caching(results)
    test_quiet_mode(results)
    
    # Error Handling
    print(f"\n{YELLOW}Error Handling:{RESET}")
    test_graphql_no_key(results)
    test_400_error(results)
    test_invalid_option(results)
    
    # Summary
    success = results.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
