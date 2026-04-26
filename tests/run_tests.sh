#!/usr/bin/env bash
#
# run_tests.sh - Run all ARC unit tests
#
# This script runs the complete test suite for ARC Linux host support.
#
# Usage:
#   ./run_tests.sh [options]
#
# Options:
#   -v, --verbose    Verbose output
#   -c, --coverage   Generate coverage report
#   -f, --fast       Skip slow tests
#   -h, --help       Show this help message

set -euo pipefail

# Colors
readonly COLOR_RED='\033[0;31m'
readonly COLOR_GREEN='\033[0;32m'
readonly COLOR_YELLOW='\033[1;33m'
readonly COLOR_BLUE='\033[0;34m'
readonly COLOR_RESET='\033[0m'

# Default options
VERBOSE=false
COVERAGE=false
FAST=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -c|--coverage)
            COVERAGE=true
            shift
            ;;
        -f|--fast)
            FAST=true
            shift
            ;;
        -h|--help)
            head -n 15 "$0" | tail -n +3 | sed 's/^# //'
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo -e "${COLOR_BLUE}=== ARC Linux Host Support - Test Suite ===${COLOR_RESET}"
echo ""

# Check if pytest is installed
if ! command -v pytest &>/dev/null; then
    echo -e "${COLOR_RED}Error: pytest not found${COLOR_RESET}"
    echo "Install with: pip3 install pytest pytest-mock pytest-cov"
    exit 1
fi

# Build pytest command
PYTEST_CMD="pytest tests/"

if [[ "$VERBOSE" == true ]]; then
    PYTEST_CMD="$PYTEST_CMD -v"
fi

if [[ "$COVERAGE" == true ]]; then
    PYTEST_CMD="$PYTEST_CMD --cov=core --cov=services --cov-report=html --cov-report=term"
fi

if [[ "$FAST" == true ]]; then
    PYTEST_CMD="$PYTEST_CMD -m 'not slow'"
fi

# Add color and short traceback
PYTEST_CMD="$PYTEST_CMD --color=yes --tb=short"

echo -e "${COLOR_BLUE}Running: $PYTEST_CMD${COLOR_RESET}"
echo ""

# Run tests
if $PYTEST_CMD; then
    echo ""
    echo -e "${COLOR_GREEN}✓ All tests passed!${COLOR_RESET}"
    
    if [[ "$COVERAGE" == true ]]; then
        echo ""
        echo -e "${COLOR_BLUE}Coverage report generated: htmlcov/index.html${COLOR_RESET}"
    fi
    
    exit 0
else
    echo ""
    echo -e "${COLOR_RED}✗ Some tests failed${COLOR_RESET}"
    exit 1
fi
