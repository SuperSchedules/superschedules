#!/bin/bash

# Superschedules Multi-Repository Test Runner
# Runs tests across all 4 repos and provides consolidated reporting

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="/home/gregk/superschedules_frontend"
COLLECTOR_DIR="/home/gregk/superschedules_collector"
NAVIGATOR_DIR="/home/gregk/superschedules_navigator"

# Test results tracking
declare -A test_results
declare -A test_times
declare -A test_details


# Command line options
SELECTED_REPOS=""
WATCH_MODE=false
CI_MODE=false
DEBUG_MODE=false
PYTEST_THREADS="8"  # Optimal for 5800X3D - reliable performance with headroom

print_usage() {
    echo -e "${BLUE}Superschedules Multi-Repository Test Runner${NC}\n"
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --repo=REPO        Run tests for specific repo only (api|frontend|collector|navigator|all)"
    echo "  --threads=N        Number of pytest threads (default: auto)"
    echo "  --watch            Watch mode - re-run tests on file changes"
    echo "  --ci               CI mode - stricter reporting, fail fast"
    echo "  --debug            Debug mode - show detailed output"
    echo "  --help             Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                     # Run all tests"
    echo "  $0 --repo=frontend     # Run only frontend tests"
    echo "  $0 --threads=16        # Run with 16 pytest threads"
    echo "  $0 --ci               # Run in CI mode"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --repo=*)
                SELECTED_REPOS="${1#*=}"
                shift
                ;;
            --threads=*)
                PYTEST_THREADS="${1#*=}"
                shift
                ;;
            --watch)
                WATCH_MODE=true
                shift
                ;;
            --ci)
                CI_MODE=true
                shift
                ;;
            --debug)
                DEBUG_MODE=true
                shift
                ;;
            --help)
                print_usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                print_usage
                exit 1
                ;;
        esac
    done
}

print_header() {
    echo -e "${BLUE}${BOLD}🧪 Superschedules Test Suite${NC}"
    echo -e "${BLUE}${BOLD}============================${NC}\n"

    if [[ "$CI_MODE" == "true" ]]; then
        echo -e "${YELLOW}Running in CI mode${NC}"
    fi

    echo ""
}

print_status() {
    local repo="$1"
    local status="$2"
    local message="$3"
    local details="$4"

    case $status in
        "running")
            echo -e "${YELLOW}⏳ $repo:${NC} $message"
            ;;
        "pass")
            echo -e "${GREEN}✅ $repo:${NC} $message"
            ;;
        "fail")
            echo -e "${RED}❌ $repo:${NC} $message"
            if [[ -n "$details" && "$DEBUG_MODE" == "true" ]]; then
                echo -e "${RED}   Details: $details${NC}"
            fi
            ;;
        "skip")
            echo -e "${YELLOW}⏭️  $repo:${NC} $message"
            ;;
        "info")
            echo -e "${BLUE}ℹ️  $repo:${NC} $message"
            ;;
    esac
}

check_dependencies() {
    local repo="$1"
    local dir="$2"

    if [[ ! -d "$dir" ]]; then
        print_status "$repo" "fail" "Directory not found: $dir"
        return 1
    fi

    case $repo in
        "api"|"collector"|"navigator")
            if [[ ! -f "$dir/.venv/bin/activate" ]]; then
                print_status "$repo" "fail" "Python virtual environment not found"
                return 1
            fi
            ;;
        "frontend")
            if ! command -v pnpm >/dev/null 2>&1; then
                print_status "$repo" "fail" "pnpm not found"
                return 1
            fi
            if [[ ! -d "$dir/node_modules" ]]; then
                print_status "$repo" "info" "Installing dependencies..."
                cd "$dir" && pnpm install >/dev/null 2>&1
            fi
            ;;
    esac
    return 0
}

run_api_tests() {
    local start_time=$(date +%s.%3N)

    print_status "api" "running" "Running Django tests..."

    cd "$BASE_DIR"

    if ! check_dependencies "api" "$BASE_DIR"; then
        test_results["api"]="fail"
        return 1
    fi

    local output
    local exit_code

    # Create unique test database name to avoid conflicts in parallel runs
    local test_db_suffix="_$$_$(date +%s%3N)"
    export TEST_DB_SUFFIX="$test_db_suffix"

    if [[ "$DEBUG_MODE" == "true" ]]; then
        "$BASE_DIR/.venv/bin/python" manage.py test --settings=config.test_settings --parallel "$PYTEST_THREADS" --verbosity=2
        exit_code=$?
    else
        output=$("$BASE_DIR/.venv/bin/python" manage.py test --settings=config.test_settings --parallel "$PYTEST_THREADS" 2>&1)
        exit_code=$?
    fi

    local end_time=$(date +%s.%3N)
    test_times["api"]=$(echo "$end_time - $start_time" | bc)

    if [[ $exit_code -eq 0 ]]; then
        local test_count
        test_count=$(echo "$output" | grep -o "Ran [0-9]* test" | grep -o "[0-9]*" || echo "unknown")
        print_status "api" "pass" "All tests passed ($test_count tests)"
        test_results["api"]="pass"
        test_details["api"]="$test_count tests passed"
    else
        print_status "api" "fail" "Tests failed"
        test_results["api"]="fail"
        test_details["api"]="$output"

        if [[ "$CI_MODE" == "true" ]]; then
            echo -e "${RED}API test failures:${NC}"
            echo "$output"
        fi
    fi

    return $exit_code
}

run_frontend_tests() {
    local start_time=$(date +%s.%3N)

    print_status "frontend" "running" "Running React tests..."

    cd "$FRONTEND_DIR"

    if ! check_dependencies "frontend" "$FRONTEND_DIR"; then
        test_results["frontend"]="fail"
        return 1
    fi

    local output
    local exit_code

    if [[ "$DEBUG_MODE" == "true" ]]; then
        pnpm run test --run
        exit_code=$?
    else
        output=$(pnpm run test --run 2>&1)
        exit_code=$?
    fi

    local end_time=$(date +%s.%3N)
    test_times["frontend"]=$(echo "$end_time - $start_time" | bc)

    if [[ $exit_code -eq 0 ]]; then
        local test_info
        test_info=$(echo "$output" | grep -E "(Test Files|Tests)" | tail -2 | tr '\n' ', ' || echo "completed")
        print_status "frontend" "pass" "All tests passed ($test_info)"
        test_results["frontend"]="pass"
        test_details["frontend"]="$test_info"
    else
        print_status "frontend" "fail" "Tests failed"
        test_results["frontend"]="fail"
        test_details["frontend"]="$output"

        if [[ "$CI_MODE" == "true" ]]; then
            echo -e "${RED}Frontend test failures:${NC}"
            echo "$output"
        fi
    fi

    return $exit_code
}

run_collector_tests() {
    local start_time=$(date +%s.%3N)

    print_status "collector" "running" "Running collector tests..."

    cd "$COLLECTOR_DIR"

    if ! check_dependencies "collector" "$COLLECTOR_DIR"; then
        test_results["collector"]="fail"
        return 1
    fi

    local output
    local exit_code

    # Prevent playwright from trying to launch browsers in background processes
    export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
    export PLAYWRIGHT_BROWSERS_PATH=0

    if [[ "$DEBUG_MODE" == "true" ]]; then
        "$COLLECTOR_DIR/.venv/bin/pytest" -v -n "$PYTEST_THREADS"
        exit_code=$?
    else
        output=$("$COLLECTOR_DIR/.venv/bin/pytest" -n "$PYTEST_THREADS" 2>&1)
        exit_code=$?
    fi

    local end_time=$(date +%s.%3N)
    test_times["collector"]=$(echo "$end_time - $start_time" | bc)

    if [[ $exit_code -eq 0 ]]; then
        local test_count
        test_count=$(echo "$output" | grep -o "[0-9]* passed" | grep -o "[0-9]*" || echo "unknown")
        print_status "collector" "pass" "All tests passed ($test_count tests)"
        test_results["collector"]="pass"
        test_details["collector"]="$test_count tests passed"
    elif [[ $exit_code -eq 1 ]]; then
        # Exit code 1 means some tests failed but pytest ran successfully
        local test_summary
        test_summary=$(echo "$output" | grep -E "failed.*passed" | tail -1 || echo "Tests completed with failures")
        print_status "collector" "fail" "Tests failed ($test_summary)"
        test_results["collector"]="fail"
        test_details["collector"]="$test_summary"

        if [[ "$CI_MODE" == "true" ]]; then
            echo -e "${RED}Collector test failures:${NC}"
            echo "$output"
        fi
    else
        # Exit code > 1 means pytest itself failed (syntax error, etc.)
        print_status "collector" "fail" "Tests failed to run"
        test_results["collector"]="fail"
        test_details["collector"]="$output"

        if [[ "$CI_MODE" == "true" ]]; then
            echo -e "${RED}Collector test failures:${NC}"
            echo "$output"
        fi
    fi

    return $exit_code
}

run_navigator_tests() {
    local start_time=$(date +%s.%3N)

    print_status "navigator" "running" "Running navigator tests..."

    cd "$NAVIGATOR_DIR"

    if ! check_dependencies "navigator" "$NAVIGATOR_DIR"; then
        test_results["navigator"]="fail"
        return 1
    fi

    local output
    local exit_code

    if [[ "$DEBUG_MODE" == "true" ]]; then
        "$NAVIGATOR_DIR/.venv/bin/pytest" -v -n "$PYTEST_THREADS"
        exit_code=$?
    else
        output=$("$NAVIGATOR_DIR/.venv/bin/pytest" -n "$PYTEST_THREADS" 2>&1)
        exit_code=$?
    fi

    local end_time=$(date +%s.%3N)
    test_times["navigator"]=$(echo "$end_time - $start_time" | bc)

    if [[ $exit_code -eq 0 ]]; then
        local test_info
        test_info=$(echo "$output" | grep -E "passed|skipped" | tail -1 || echo "completed")
        print_status "navigator" "pass" "Tests completed ($test_info)"
        test_results["navigator"]="pass"
        test_details["navigator"]="$test_info"
    else
        print_status "navigator" "fail" "Tests failed"
        test_results["navigator"]="fail"
        test_details["navigator"]="$output"

        if [[ "$CI_MODE" == "true" ]]; then
            echo -e "${RED}Navigator test failures:${NC}"
            echo "$output"
        fi
    fi

    return $exit_code
}


run_tests_sequential() {
    local repos=("$@")
    local overall_result=0

    for repo in "${repos[@]}"; do
        case $repo in
            "api")
                if ! run_api_tests; then overall_result=1; fi
                ;;
            "frontend")
                if ! run_frontend_tests; then overall_result=1; fi
                ;;
            "collector")
                if ! run_collector_tests; then overall_result=1; fi
                ;;
            "navigator")
                if ! run_navigator_tests; then overall_result=1; fi
                ;;
        esac

        if [[ $overall_result -ne 0 && "$CI_MODE" == "true" ]]; then
            echo -e "${RED}Stopping due to test failure in CI mode${NC}"
            break
        fi
    done

    return $overall_result
}

print_summary() {
    echo -e "\n${BLUE}${BOLD}📊 Test Summary${NC}"
    echo -e "${BLUE}${BOLD}===============${NC}\n"

    local total_tests=0
    local passed_tests=0
    local failed_tests=0

    for repo in "${!test_results[@]}"; do
        local result="${test_results[$repo]}"
        local time="${test_times[$repo]:-0}"
        local details="${test_details[$repo]:-}"

        case $result in
            "pass")
                echo -e "${GREEN}✅ $repo${NC} (${time}s) - $details"
                passed_tests=$((passed_tests + 1))
                ;;
            "fail")
                echo -e "${RED}❌ $repo${NC} (${time}s) - FAILED"
                failed_tests=$((failed_tests + 1))
                if [[ "$DEBUG_MODE" == "true" && -n "$details" ]]; then
                    echo -e "${RED}   Error details: ${details:0:200}...${NC}"
                fi
                ;;
        esac
        total_tests=$((total_tests + 1))
    done

    echo ""
    if [[ $failed_tests -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}🎉 All $passed_tests repository test suites passed!${NC}"
        return 0
    else
        echo -e "${RED}${BOLD}💥 $failed_tests of $total_tests repository test suites failed${NC}"
        return 1
    fi
}

determine_repos() {
    if [[ -n "$SELECTED_REPOS" ]]; then
        case $SELECTED_REPOS in
            "all")
                echo "collector api frontend navigator"
                ;;
            "api"|"frontend"|"collector"|"navigator")
                echo "$SELECTED_REPOS"
                ;;
            *)
                echo "Invalid repo: $SELECTED_REPOS" >&2
                echo "Valid options: api, frontend, collector, navigator, all" >&2
                exit 1
                ;;
        esac
    else
        echo "api frontend collector navigator"
    fi
}

main() {
    parse_args "$@"
    print_header

    local repos
    repos=$(determine_repos)
    read -ra repo_array <<< "$repos"

    local start_time=$(date +%s)

    run_tests_sequential "${repo_array[@]}"

    local test_result=$?
    local end_time=$(date +%s)
    local total_time=$((end_time - start_time))

    echo -e "\n${BLUE}Total execution time: ${total_time}s${NC}"

    if ! print_summary; then
        exit 1
    fi

    exit $test_result
}

# Check for bc command (needed for time calculations)
if ! command -v bc >/dev/null 2>&1; then
    echo -e "${YELLOW}Warning: bc command not found, test times may not be accurate${NC}"
    # Fallback for time calculations
    bc() { echo "0"; }
    export -f bc
fi

# Run main function
main "$@"