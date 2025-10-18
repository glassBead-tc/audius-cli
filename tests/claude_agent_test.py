#!/usr/bin/env python3
"""
Claude Agent SDK-based testing for Audius CLI.

This test uses Claude Agent SDK to create an intelligent testing agent that can:
- Understand CLI responses and adapt testing strategy
- Discover edge cases through exploration
- Generate comprehensive test reports
- Learn from failures and retry intelligently
"""
import asyncio
import json
import os
import sys
from pathlib import Path

try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

# Path to CLI
CLI_PATH = Path(__file__).parent.parent / "audius-cli.py"


AGENT_SYSTEM_PROMPT = """You are a QA testing agent for the Audius CLI, a command-line tool for interacting with the Audius music platform.

Your goal is to thoroughly test the CLI by running various commands and validating their behavior.

## Available CLI Features

**Command Groups:**
- tracks: Track-related operations (get_trending_tracks, search_tracks, etc.)
- users: User operations (get_user, search_users, etc.)
- playlists: Playlist operations
- graphql: GraphQL queries for governance data (requires API key)
- challenges, comments, tips, resolve, developer_apps, dashboard_wallet_users

**Global Options:**
- --format (pretty/compact/raw): Output format
- --output FILE: Save to file
- --quiet: Show only data, no metadata
- --cache N: Cache responses for N seconds
- --select FIELDS: Select specific fields (e.g., "title,user.handle,play_count")
- --graphql-api-key KEY: API key for GraphQL (or set AUDIUS_GRAPHQL_KEY env var)

## Testing Strategy

1. **Basic Functionality**: Test that commands run and return valid JSON
2. **Features**: Test caching, field selection, output formats
3. **Error Handling**: Test invalid inputs, missing resources (404s), etc.
4. **Integration**: Test real API calls work correctly
5. **Edge Cases**: Discover and test edge cases

## What to Test

- Run `python audius-cli.py --help` to see available commands
- Test REST API calls like: `python audius-cli.py tracks get_trending_tracks --time week`
- Test features like: `python audius-cli.py --select 'title,play_count' --quiet tracks get_trending_tracks --time week`
- Test caching: Run same command twice with `--cache 60` and verify "[From cache]" appears
- Test error messages: Try invalid track IDs and verify helpful errors
- Test GraphQL without API key and verify helpful error message

## Success Criteria

- Commands execute without crashes
- JSON output is valid
- Features work as documented (caching, field selection, formats)
- Error messages are helpful and actionable
- No data corruption or unexpected behavior

## Output Format

For each test, report:
1. What you're testing
2. The command you ran
3. Whether it passed or failed
4. Any issues discovered

Be thorough but efficient. Focus on high-value tests that verify core functionality and common use cases.
"""


async def run_cli_tests_with_agent():
    """Run CLI tests using Claude Agent SDK."""
    if not HAS_ANTHROPIC:
        print("Error: anthropic package not installed")
        print("Install with: pip install anthropic")
        return False
    
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set")
        print("Get your API key from: https://console.anthropic.com/")
        return False
    
    client = Anthropic(api_key=api_key)
    
    print("="*70)
    print("Audius CLI - Claude Agent SDK Testing")
    print("="*70)
    print(f"\nCLI Path: {CLI_PATH}")
    print(f"Working Directory: {Path.cwd()}")
    print("\nStarting intelligent test agent...\n")
    
    # Create a conversation with the agent
    messages = [
        {
            "role": "user",
            "content": f"""Test the Audius CLI located at: {CLI_PATH}

Run comprehensive tests covering:
1. Basic functionality (--help, command groups)
2. REST API integration (real API calls)
3. Features (caching, field selection, formats)
4. Error handling (404s, invalid inputs)

For each test:
- Describe what you're testing
- Show the exact command
- Report pass/fail with reasoning
- Note any issues

You can run bash commands to test the CLI. Use the bash tool to execute commands.

Start by running --help to understand the CLI, then proceed with systematic testing.
"""
        }
    ]
    
    # Run the agent conversation
    max_turns = 10
    turn = 0
    
    while turn < max_turns:
        turn += 1
        print(f"\n{'='*70}")
        print(f"Agent Turn {turn}")
        print(f"{'='*70}\n")
        
        try:
            response = client.messages.create(
                model="claude-opus-4-20250514",
                max_tokens=8000,
                system=AGENT_SYSTEM_PROMPT,
                messages=messages,
                tools=[
                    {
                        "name": "bash",
                        "description": "Execute bash commands to test the CLI. Returns stdout, stderr, and exit code.",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": "The bash command to execute"
                                }
                            },
                            "required": ["command"]
                        }
                    }
                ]
            )
            
            # Add assistant response to messages
            messages.append({
                "role": "assistant",
                "content": response.content
            })
            
            # Check if agent is done (no tool use)
            has_tool_use = any(block.type == "tool_use" for block in response.content)
            
            if not has_tool_use:
                # Agent is done, print final response
                for block in response.content:
                    if block.type == "text":
                        print(block.text)
                break
            
            # Process tool calls
            tool_results = []
            for block in response.content:
                if block.type == "text":
                    print(block.text)
                elif block.type == "tool_use":
                    if block.name == "bash":
                        command = block.input["command"]
                        print(f"\n🔧 Running: {command}")
                        
                        # Execute command
                        import subprocess
                        try:
                            result = subprocess.run(
                                command,
                                shell=True,
                                capture_output=True,
                                text=True,
                                timeout=30
                            )
                            
                            output = {
                                "exit_code": result.returncode,
                                "stdout": result.stdout,
                                "stderr": result.stderr
                            }
                            
                            # Show output
                            if result.stdout:
                                print(f"📤 Output:\n{result.stdout[:500]}")
                            if result.stderr:
                                print(f"⚠️  Stderr:\n{result.stderr[:500]}")
                            
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(output)
                            })
                        except subprocess.TimeoutExpired:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps({
                                    "exit_code": -1,
                                    "error": "Command timed out after 30 seconds"
                                })
                            })
                        except Exception as e:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps({
                                    "exit_code": -1,
                                    "error": str(e)
                                })
                            })
            
            # Add tool results to messages
            if tool_results:
                messages.append({
                    "role": "user",
                    "content": tool_results
                })
        
        except Exception as e:
            print(f"Error during agent execution: {e}")
            return False
    
    print(f"\n{'='*70}")
    print("Testing Complete")
    print(f"{'='*70}\n")
    
    return True


def main():
    """Entry point for Claude Agent SDK tests."""
    if not HAS_ANTHROPIC:
        print("\n⚠️  Claude Agent SDK Testing Requires anthropic package")
        print("\nInstall with:")
        print("  pip install anthropic")
        print("\nThen set your API key:")
        print("  export ANTHROPIC_API_KEY=your-key-here")
        print("\nGet your API key from: https://console.anthropic.com/")
        print("\nFor now, running fallback subprocess tests...")
        print("\nRun: python tests/agent_test.py")
        sys.exit(1)
    
    # Run agent-based tests
    success = asyncio.run(run_cli_tests_with_agent())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
