# WatchBuddy Test Scripts

This directory contains test and debug scripts that are used for development and debugging purposes. These scripts are **excluded from production Docker builds**.

## Directory Structure

### `/unit/` - Unit Tests
Actual unit tests for specific functionality:

- `test_mood*.py` - Tests for mood analysis and scoring functionality
- `test_timezone.py` - Tests for timezone handling

### `/debug/` - Debug and Analysis Scripts
Scripts for debugging specific issues, analyzing data, and troubleshooting:

- `check_*.py` - Scripts to check the state of specific lists, items, and user data
- `debug_*.py` - Debug scripts for specific functionality (enrichment, sync, etc.)
- `analyze_*.py` - Analysis scripts for understanding data patterns and issues
- `test_enhanced_filtering.py` - Test enhanced pool size filtering functionality
- `test_ultra_discovery.py` - Test ultra discovery mode for candidate generation

### `/manual/` - Manual Testing Scripts
Scripts for manual testing and triggering specific operations:

- `trigger_sync.py` - Manually trigger list synchronization for testing
- `direct_sync.py` - Direct sync operations bypassing normal workflow  
- `test_search.py` - Manual testing of search functionality
- `test_candidate_discovery.py` - Manual testing of candidate discovery algorithms

## Usage

### Running Unit Tests
Unit tests can be run with pytest or directly:

```bash
# Run all unit tests
docker exec -i watchbuddy-backend-1 python -m pytest /app/tests/unit/

# Run specific test
docker exec -i watchbuddy-backend-1 python /app/tests/unit/test_mood.py
```

### Running Debug Scripts
Debug scripts are designed to be run from inside the Docker container:

```bash
# Debug specific list
docker exec -i watchbuddy-backend-1 python /app/tests/debug/check_lists.py

# Analyze enrichment issues
docker exec -i watchbuddy-backend-1 python /app/tests/debug/analyze_enrichment.py
```

### Running Manual Tests
Manual test scripts for triggering operations:

```bash
# Trigger sync for a specific list
docker exec -i watchbuddy-backend-1 python /app/tests/manual/trigger_sync.py <list_id>

# Test search functionality
docker exec -i watchbuddy-backend-1 python /app/tests/manual/test_search.py
```

## Important Notes

- **Not included in production**: These scripts are excluded from Docker builds and GitHub Actions
- **Development only**: Scripts contain hardcoded paths and debug outputs
- **Container execution**: Scripts should be run from inside the Docker container for proper environment setup
- **Path requirements**: Scripts expect to be run with `PYTHONPATH=/app` for proper imports

## Adding New Test Scripts

When adding new test/debug scripts:

1. Place in appropriate subdirectory (`debug/` or `manual/`)
2. Include proper shebang: `#!/usr/bin/env python3`
3. Add path setup: `sys.path.append('/app')`
4. Document usage in this README
5. Ensure scripts are excluded from production builds