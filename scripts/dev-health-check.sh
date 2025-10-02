#!/bin/bash

# Superschedules Health Check Script
# Verifies all development services are running and healthy

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Service ports
DJANGO_PORT=8000
CHAT_PORT=8002
COLLECTOR_PORT=8001
NAVIGATOR_PORT=8004
FRONTEND_PORT=5173

print_header() {
    echo -e "${BLUE}🩺 Superschedules Health Check${NC}"
    echo -e "${BLUE}==============================${NC}\n"
}

check_service() {
    local service="$1"
    local url="$2"
    local timeout=5

    if curl -sf --max-time $timeout "$url" >/dev/null 2>&1; then
        echo -e "${GREEN}✅ $service:${NC} Healthy at $url"
        return 0
    else
        echo -e "${RED}❌ $service:${NC} Not responding at $url"
        return 1
    fi
}

check_postgresql() {
    echo -e "${BLUE}🔍 Checking PostgreSQL...${NC}"

    if ! pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
        echo -e "${RED}❌ PostgreSQL:${NC} Not running"
        return 1
    fi

    # Check pgvector extension
    local vector_check
    vector_check=$(psql -d superschedules -t -c "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector');" 2>/dev/null || echo "f")
    vector_check=$(echo "$vector_check" | tr -d ' ')  # Remove whitespace

    if [[ "$vector_check" == "t" ]]; then
        echo -e "${GREEN}✅ PostgreSQL:${NC} Running with pgvector extension"
    else
        echo -e "${YELLOW}⚠️  PostgreSQL:${NC} Running but pgvector extension missing"
        return 1
    fi

    return 0
}

check_ollama() {
    echo -e "${BLUE}🔍 Checking Ollama...${NC}"

    if ! command -v ollama >/dev/null 2>&1; then
        echo -e "${RED}❌ Ollama:${NC} Command not found"
        return 1
    fi

    if ! ollama list >/dev/null 2>&1; then
        echo -e "${RED}❌ Ollama:${NC} Not responding"
        return 1
    fi

    # Check if deepseek model is available
    if ollama list | grep -q "deepseek-llm:7b"; then
        echo -e "${GREEN}✅ Ollama:${NC} Running with deepseek-llm:7b"
    else
        echo -e "${YELLOW}⚠️  Ollama:${NC} Running but deepseek-llm:7b not available"
    fi

    return 0
}

main() {
    print_header

    local overall_health=0

    # Check infrastructure
    if ! check_postgresql; then overall_health=1; fi
    if ! check_ollama; then overall_health=1; fi

    echo ""

    # Check application services
    if ! check_service "Django API" "http://localhost:$DJANGO_PORT/api/live"; then overall_health=1; fi
    if ! check_service "Chat Service" "http://localhost:$CHAT_PORT/health"; then overall_health=1; fi
    if ! check_service "Collector Service" "http://localhost:$COLLECTOR_PORT/health"; then overall_health=1; fi
    if ! check_service "Navigator Service" "http://localhost:$NAVIGATOR_PORT/health"; then overall_health=1; fi
    if ! check_service "Frontend" "http://localhost:$FRONTEND_PORT"; then overall_health=1; fi

    echo ""

    if [[ $overall_health -eq 0 ]]; then
        echo -e "${GREEN}🎉 All services are healthy!${NC}"
    else
        echo -e "${RED}💥 Some services are not healthy${NC}"
        echo -e "${BLUE}💡 Run: ./scripts/dev-start.sh to start missing services${NC}"
    fi

    exit $overall_health
}

main "$@"