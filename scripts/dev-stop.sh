#!/bin/bash

# Superschedules Development Environment Stop Script
# Stops all running development services

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# PID file location
PID_FILE="/tmp/superschedules-dev.pids"

print_header() {
    echo -e "${BLUE}🛑 Stopping Superschedules Development Services${NC}"
    echo -e "${BLUE}===============================================${NC}\n"
}

stop_by_port() {
    local port="$1"
    local service="$2"

    echo -e "${YELLOW}🔍 Checking for $service on port $port...${NC}"

    local pids
    pids=$(lsof -ti:$port 2>/dev/null || true)

    if [[ -n "$pids" ]]; then
        echo -e "${YELLOW}⏹️  Stopping $service (PIDs: $pids)${NC}"
        echo "$pids" | xargs -r kill -TERM 2>/dev/null || true
        sleep 2

        # Force kill if still running
        pids=$(lsof -ti:$port 2>/dev/null || true)
        if [[ -n "$pids" ]]; then
            echo -e "${RED}💀 Force killing $service (PIDs: $pids)${NC}"
            echo "$pids" | xargs -r kill -KILL 2>/dev/null || true
        fi

        echo -e "${GREEN}✅ $service stopped${NC}"
    else
        echo -e "${BLUE}ℹ️  $service not running${NC}"
    fi
}

stop_by_process_name() {
    local process_pattern="$1"
    local service="$2"

    echo -e "${YELLOW}🔍 Checking for $service processes...${NC}"

    local pids
    pids=$(pgrep -f "$process_pattern" 2>/dev/null || true)

    if [[ -n "$pids" ]]; then
        echo -e "${YELLOW}⏹️  Stopping $service (PIDs: $pids)${NC}"
        echo "$pids" | xargs -r kill -TERM 2>/dev/null || true
        sleep 2

        # Force kill if still running
        pids=$(pgrep -f "$process_pattern" 2>/dev/null || true)
        if [[ -n "$pids" ]]; then
            echo -e "${RED}💀 Force killing $service (PIDs: $pids)${NC}"
            echo "$pids" | xargs -r kill -KILL 2>/dev/null || true
        fi

        echo -e "${GREEN}✅ $service stopped${NC}"
    else
        echo -e "${BLUE}ℹ️  $service not running${NC}"
    fi
}

stop_tracked_processes() {
    if [[ -f "$PID_FILE" ]]; then
        echo -e "${YELLOW}📝 Stopping tracked processes from $PID_FILE...${NC}"

        while IFS= read -r pid; do
            if [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]]; then
                if kill -0 "$pid" 2>/dev/null; then
                    echo -e "${YELLOW}⏹️  Stopping process $pid${NC}"
                    kill -TERM "$pid" 2>/dev/null || true
                else
                    echo -e "${BLUE}ℹ️  Process $pid already stopped${NC}"
                fi
            fi
        done < "$PID_FILE"

        # Wait a moment for graceful shutdown
        sleep 2

        # Force kill any remaining tracked processes
        while IFS= read -r pid; do
            if [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]]; then
                if kill -0 "$pid" 2>/dev/null; then
                    echo -e "${RED}💀 Force killing process $pid${NC}"
                    kill -KILL "$pid" 2>/dev/null || true
                fi
            fi
        done < "$PID_FILE"

        rm -f "$PID_FILE"
        echo -e "${GREEN}✅ Cleaned up PID tracking file${NC}"
    else
        echo -e "${BLUE}ℹ️  No PID tracking file found${NC}"
    fi
}

cleanup_node_processes() {
    echo -e "${YELLOW}🔍 Cleaning up any remaining Node.js processes...${NC}"

    # Look for pnpm dev processes
    local node_pids
    node_pids=$(pgrep -f "node.*vite" 2>/dev/null || true)

    if [[ -n "$node_pids" ]]; then
        echo -e "${YELLOW}⏹️  Stopping Node.js/Vite processes (PIDs: $node_pids)${NC}"
        echo "$node_pids" | xargs -r kill -TERM 2>/dev/null || true
        sleep 2

        # Force kill if needed
        node_pids=$(pgrep -f "node.*vite" 2>/dev/null || true)
        if [[ -n "$node_pids" ]]; then
            echo "$node_pids" | xargs -r kill -KILL 2>/dev/null || true
        fi
        echo -e "${GREEN}✅ Node.js processes cleaned up${NC}"
    fi
}

main() {
    print_header

    # Stop tracked processes first (most reliable)
    stop_tracked_processes

    echo ""

    # Stop services by port (backup method)
    stop_by_port 8000 "Django API"
    stop_by_port 8002 "Chat Service"
    stop_by_port 8001 "Collector Service"
    stop_by_port 8004 "Navigator Service"
    stop_by_port 5173 "Frontend"

    echo ""

    # Stop by process patterns (final cleanup)
    stop_by_process_name "manage.py runserver" "Django runserver"
    stop_by_process_name "uvicorn.*chat_service" "FastAPI Chat Service"
    stop_by_process_name "start_api.py" "Collector/Navigator APIs"

    # Clean up any remaining Node.js processes
    cleanup_node_processes

    echo ""
    echo -e "${GREEN}🎉 All development services stopped${NC}"
    echo -e "${BLUE}💡 Services can be restarted with: ./scripts/dev-start.sh${NC}"
}

main "$@"