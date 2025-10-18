# Audius CLI Tests

This directory contains two types of tests for the Audius CLI:

## 1. Subprocess-Based Tests (`agent_test.py`)

Traditional integration tests that run CLI commands via subprocess and validate outputs.

**Run:**
```bash
python tests/agent_test.py
```

**Features:**
- ✅ No API keys required
- ✅ Fast execution
- ✅ Validates basic functionality, features, and error handling
- ✅ Tests REST API integration with real Audius API calls

**What it tests:**
- CLI help and command groups
- REST API calls (trending tracks)
- Field selection with `--select`
- Output formats (pretty/compact/raw)
- File output with `--output`
- Response caching with `--cache`
- Quiet mode with `--quiet`
- Error handling (404s, GraphQL without key, invalid options)

## 2. Claude Agent SDK Tests (`claude_agent_test.py`)

Intelligent AI agent that tests the CLI by understanding responses and adapting strategy.

**Setup:**
```bash
# Install dependencies
pip install anthropic

# Set API key
export ANTHROPIC_API_KEY=your-key-here
```

**Run:**
```bash
python tests/claude_agent_test.py
```

**Features:**
- 🤖 AI agent understands CLI behavior
- 🔍 Discovers edge cases through exploration
- 📊 Generates comprehensive test reports
- 🧠 Learns from failures and retries intelligently
- 💡 Can suggest improvements based on findings

**What it does:**
- Explores CLI systematically using bash tool
- Validates JSON responses and error messages
- Tests feature combinations intelligently
- Discovers unexpected behaviors
- Provides detailed analysis of issues

## Quick Test

Run the subprocess tests (no setup required):
```bash
python tests/agent_test.py
```

Expected output:
```
============================================================
Audius CLI - Agent-Based Integration Tests
============================================================

Basic Functionality:
✓ CLI help displays correctly
✓ Command group 'tracks' available
✓ Command group 'users' available
...

Test Summary
============================================================
Total:   18
Passed:  18
Failed:  0
Skipped: 0
```

## Test Philosophy

These tests follow an **agent-based approach** where tests act like real users:
- Run actual CLI commands
- Validate real API responses
- Test real-world scenarios
- No mocking (except where API keys are unavailable)

This ensures the CLI works correctly in production environments.
