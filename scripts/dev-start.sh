#!/bin/bash

# Superschedules Development Environment Starter
# Starts all services locally and verifies they're healthy

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="/home/gregk/superschedules_frontend"
NAVIGATOR_DIR="/home/gregk/superschedules_navigator"

# Parse command-line arguments
SHOW_LOGS=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --show-logs)
            SHOW_LOGS=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--show-logs]"
            exit 1
            ;;
    esac
done

# Service ports
DJANGO_PORT=8000
CHAT_PORT=8002
NAVIGATOR_PORT=8004
FRONTEND_PORT=5173

# PID file for cleanup
PID_FILE="/tmp/superschedules-dev.pids"

# Log directory
LOG_DIR="/tmp/superschedules-logs"
mkdir -p "$LOG_DIR"

# Cleanup function
cleanup() {
    echo -e "\n${YELLOW}🧹 Cleaning up...${NC}"
    if [[ -f "$PID_FILE" ]]; then
        while IFS= read -r pid; do
            if kill -0 "$pid" 2>/dev/null; then
                echo "Killing process $pid"
                kill "$pid" 2>/dev/null || true
            fi
        done < "$PID_FILE"
        rm -f "$PID_FILE"
    fi
    echo -e "${GREEN}✅ Cleanup complete${NC}"
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

print_header() {
    echo -e "${BLUE}🚀 Superschedules Development Environment${NC}"
    echo -e "${BLUE}=========================================${NC}\n"
}

print_status() {
    local service="$1"
    local status="$2"
    local message="$3"

    case $status in
        "starting")
            echo -e "${YELLOW}⏳ $service:${NC} $message"
            ;;
        "success")
            echo -e "${GREEN}✅ $service:${NC} $message"
            ;;
        "error")
            echo -e "${RED}❌ $service:${NC} $message"
            ;;
        "info")
            echo -e "${BLUE}ℹ️  $service:${NC} $message"
            ;;
    esac
}

wait_for_service() {
    local url="$1"
    local service="$2"
    local timeout=30
    local count=0

    print_status "$service" "starting" "Waiting for service..."

    while ! curl -sf "$url" >/dev/null 2>&1; do
        sleep 1
        count=$((count + 1))
        if [[ $count -ge $timeout ]]; then
            print_status "$service" "error" "Timeout waiting for service at $url"
            return 1
        fi
    done

    print_status "$service" "success" "Service responding at $url"
    return 0
}

check_postgresql() {
    print_status "PostgreSQL" "starting" "Checking PostgreSQL..."

    # Check if PostgreSQL is running
    if ! pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
        print_status "PostgreSQL" "error" "PostgreSQL is not running. Please start it first."
        echo -e "  ${BLUE}💡 Try: sudo systemctl start postgresql${NC}"
        return 1
    fi

    # Check pgvector extension
    print_status "PostgreSQL" "starting" "Verifying pgvector extension..."

    local vector_check
    vector_check=$(psql -d superschedules -t -c "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector');" 2>/dev/null || echo "f")
    vector_check=$(echo "$vector_check" | tr -d ' ')  # Remove whitespace

    if [[ "$vector_check" == "t" ]]; then
        print_status "PostgreSQL" "success" "pgvector extension verified"
    else
        print_status "PostgreSQL" "error" "pgvector extension not found"
        echo -e "  ${BLUE}💡 Run: psql -d superschedules -c 'CREATE EXTENSION vector;'${NC}"
        return 1
    fi

    # Test vector operations
    print_status "PostgreSQL" "starting" "Testing vector operations..."

    local vector_test
    vector_test=$(psql -d superschedules -t -c "SELECT '[1,2,3]'::vector(3);" 2>/dev/null || echo "error")

    if [[ "$vector_test" != "error" ]]; then
        print_status "PostgreSQL" "success" "Vector operations working"
    else
        print_status "PostgreSQL" "error" "Vector operations failed"
        return 1
    fi

    return 0
}

check_ollama() {
    print_status "Ollama" "starting" "Checking Ollama..."

    if ! command -v ollama >/dev/null 2>&1; then
        print_status "Ollama" "error" "Ollama command not found"
        return 1
    fi

    if ! ollama list >/dev/null 2>&1; then
        print_status "Ollama" "error" "Ollama not responding"
        echo -e "  ${BLUE}💡 Try: ollama serve${NC}"
        return 1
    fi

    # Check if deepseek model is available
    if ollama list | grep -q "deepseek-llm:7b"; then
        print_status "Ollama" "success" "deepseek-llm:7b model available"
    else
        print_status "Ollama" "info" "deepseek-llm:7b not found, but other models available"
    fi

    return 0
}

start_django() {
    print_status "Django API" "starting" "Starting Django development server..."

    cd "$BASE_DIR"

    # Check if virtual environment exists
    if [[ ! -d ".venv" ]]; then
        print_status "Django API" "error" "Virtual environment not found at $BASE_DIR/.venv"
        return 1
    fi

    # Run migrations
    source .venv/bin/activate
    python manage.py migrate --noinput >/dev/null 2>&1 || true

    # Start Django server
    python manage.py runserver $DJANGO_PORT >> "$LOG_DIR/django.log" 2>&1 &
    local pid=$!
    echo "$pid" >> "$PID_FILE"

    # Wait for service
    if wait_for_service "http://localhost:$DJANGO_PORT/api/live" "Django API"; then
        print_status "Django API" "success" "Running on http://localhost:$DJANGO_PORT"
        return 0
    else
        return 1
    fi
}

start_chat_service() {
    print_status "Chat Service" "starting" "Starting FastAPI chat service..."

    cd "$BASE_DIR"
    source .venv/bin/activate

    python -m uvicorn chat_service.app:app --host 0.0.0.0 --port $CHAT_PORT >/dev/null 2>&1 &
    local pid=$!
    echo "$pid" >> "$PID_FILE"

    if wait_for_service "http://localhost:$CHAT_PORT/api/v1/chat/health" "Chat Service"; then
        print_status "Chat Service" "success" "Running on http://localhost:$CHAT_PORT"
        return 0
    else
        return 1
    fi
}

start_navigator() {
    print_status "Navigator Service" "starting" "Starting navigator service..."

    if [[ ! -d "$NAVIGATOR_DIR" ]]; then
        print_status "Navigator Service" "error" "Navigator directory not found: $NAVIGATOR_DIR"
        return 1
    fi

    cd "$NAVIGATOR_DIR"

    if [[ ! -d ".venv" ]]; then
        print_status "Navigator Service" "error" "Virtual environment not found in navigator"
        return 1
    fi

    .venv/bin/python start_api.py --port $NAVIGATOR_PORT >/dev/null 2>&1 &
    local pid=$!
    echo "$pid" >> "$PID_FILE"

    if wait_for_service "http://localhost:$NAVIGATOR_PORT/health" "Navigator Service"; then
        print_status "Navigator Service" "success" "Running on http://localhost:$NAVIGATOR_PORT"
        return 0
    else
        return 1
    fi
}

start_frontend() {
    print_status "Frontend" "starting" "Starting React development server..."

    if [[ ! -d "$FRONTEND_DIR" ]]; then
        print_status "Frontend" "error" "Frontend directory not found: $FRONTEND_DIR"
        return 1
    fi

    cd "$FRONTEND_DIR"

    if ! command -v pnpm >/dev/null 2>&1; then
        print_status "Frontend" "error" "pnpm not found"
        return 1
    fi

    # Install dependencies if needed
    if [[ ! -d "node_modules" ]]; then
        print_status "Frontend" "starting" "Installing npm dependencies..."
        pnpm install >/dev/null 2>&1
    fi

    pnpm run dev >/dev/null 2>&1 &
    local pid=$!
    echo "$pid" >> "$PID_FILE"

    # Frontend might take longer to start
    sleep 3

    if wait_for_service "http://localhost:$FRONTEND_PORT" "Frontend"; then
        print_status "Frontend" "success" "Running on http://localhost:$FRONTEND_PORT"
        return 0
    else
        return 1
    fi
}

print_dashboard() {
    echo -e "\n${GREEN}🎉 All services started successfully!${NC}\n"
    echo -e "${BLUE}📊 Service Dashboard:${NC}"
    echo -e "  Frontend:          http://localhost:$FRONTEND_PORT"
    echo -e "  Django API:        http://localhost:$DJANGO_PORT"
    echo -e "  Chat Service:      http://localhost:$CHAT_PORT"
    echo -e "  Navigator Service: http://localhost:$NAVIGATOR_PORT"
    echo -e "\n${BLUE}📋 Service logs: $LOG_DIR/${NC}"
    echo -e "  tail -f $LOG_DIR/django.log"
    echo -e "\n${YELLOW}Press Ctrl+C to stop all services${NC}\n"
}

# Main execution
main() {
    print_header

    # Clean up any existing PID file
    rm -f "$PID_FILE"

    # Start and verify services
    if ! check_postgresql; then exit 1; fi
    if ! check_ollama; then exit 1; fi
    if ! start_django; then exit 1; fi
    if ! start_chat_service; then exit 1; fi
    if ! start_navigator; then exit 1; fi
    if ! start_frontend; then exit 1; fi

    print_dashboard

    # Show logs if requested
    if [[ "$SHOW_LOGS" == "true" ]]; then
        echo -e "${BLUE}📄 Showing logs (Django):${NC}\n"
        tail -f "$LOG_DIR/django.log"
    else
        # Keep script running
        while true; do
            sleep 1
        done
    fi
}

# Run main function
main "$@"
