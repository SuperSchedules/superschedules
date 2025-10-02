#!/bin/bash

# Superschedules pgvector Extension Installer
# Installs and configures pgvector extension for PostgreSQL

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Database configuration
DB_NAME="superschedules"
DB_HOST="localhost"
DB_PORT="5432"
DB_USER="${USER}" # Use current system user as default

print_header() {
    echo -e "${BLUE}🔧 pgvector Extension Installer${NC}"
    echo -e "${BLUE}================================${NC}\n"
}

print_status() {
    local status="$1"
    local message="$2"

    case $status in
        "info")
            echo -e "${BLUE}ℹ️  $message${NC}"
            ;;
        "success")
            echo -e "${GREEN}✅ $message${NC}"
            ;;
        "warning")
            echo -e "${YELLOW}⚠️  $message${NC}"
            ;;
        "error")
            echo -e "${RED}❌ $message${NC}"
            ;;
    esac
}

check_postgresql() {
    print_status "info" "Checking PostgreSQL installation..."

    if ! command -v psql >/dev/null 2>&1; then
        print_status "error" "PostgreSQL client not found"
        echo -e "  ${BLUE}💡 Install with: sudo apt-get install postgresql-client${NC}"
        return 1
    fi

    if ! pg_isready -h $DB_HOST -p $DB_PORT >/dev/null 2>&1; then
        print_status "error" "PostgreSQL server not running"
        echo -e "  ${BLUE}💡 Start with: sudo systemctl start postgresql${NC}"
        return 1
    fi

    print_status "success" "PostgreSQL is running"
    return 0
}

detect_os_and_version() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        echo "$ID:$VERSION_ID"
    else
        echo "unknown"
    fi
}

install_pgvector_ubuntu() {
    local version="$1"

    print_status "info" "Installing pgvector for Ubuntu $version..."

    # Update package list
    sudo apt-get update

    case $version in
        "22.04"|"20.04")
            # For Ubuntu 20.04/22.04, install from source or use PostgreSQL APT repository
            print_status "info" "Installing build dependencies..."
            sudo apt-get install -y build-essential git postgresql-server-dev-all

            print_status "info" "Cloning and building pgvector..."
            cd /tmp
            if [[ -d pgvector ]]; then
                rm -rf pgvector
            fi
            git clone --branch v0.6.0 https://github.com/pgvector/pgvector.git
            cd pgvector
            make
            sudo make install

            print_status "success" "pgvector compiled and installed"
            ;;
        *)
            print_status "warning" "Attempting generic Ubuntu installation..."
            sudo apt-get install -y build-essential git postgresql-server-dev-all
            cd /tmp
            if [[ -d pgvector ]]; then
                rm -rf pgvector
            fi
            git clone --branch v0.6.0 https://github.com/pgvector/pgvector.git
            cd pgvector
            make
            sudo make install
            ;;
    esac
}

install_pgvector_debian() {
    print_status "info" "Installing pgvector for Debian..."

    sudo apt-get update
    sudo apt-get install -y build-essential git postgresql-server-dev-all

    cd /tmp
    if [[ -d pgvector ]]; then
        rm -rf pgvector
    fi
    git clone --branch v0.6.0 https://github.com/pgvector/pgvector.git
    cd pgvector
    make
    sudo make install

    print_status "success" "pgvector compiled and installed"
}

install_pgvector() {
    local os_info
    os_info=$(detect_os_and_version)

    print_status "info" "Detected OS: $os_info"

    case $os_info in
        ubuntu:*)
            local version=$(echo $os_info | cut -d: -f2)
            install_pgvector_ubuntu "$version"
            ;;
        debian:*)
            install_pgvector_debian
            ;;
        *)
            print_status "warning" "Unsupported OS, attempting generic installation..."
            print_status "info" "Installing build dependencies..."
            sudo apt-get update || sudo yum update || true
            sudo apt-get install -y build-essential git postgresql-server-dev-all || \
            sudo yum install -y gcc git postgresql-devel || true

            cd /tmp
            if [[ -d pgvector ]]; then
                rm -rf pgvector
            fi
            git clone --branch v0.6.0 https://github.com/pgvector/pgvector.git
            cd pgvector
            make
            sudo make install
            ;;
    esac
}

create_database() {
    print_status "info" "Checking if database '$DB_NAME' exists..."

    if psql -h $DB_HOST -p $DB_PORT -lqt | cut -d \| -f 1 | grep -qw $DB_NAME; then
        print_status "success" "Database '$DB_NAME' already exists"
    else
        print_status "info" "Creating database '$DB_NAME'..."
        createdb -h $DB_HOST -p $DB_PORT $DB_NAME
        print_status "success" "Database '$DB_NAME' created"
    fi
}

install_extension() {
    print_status "info" "Installing pgvector extension in database..."

    # Check if extension is already installed
    local ext_check
    ext_check=$(psql -h $DB_HOST -p $DB_PORT -d $DB_NAME -t -c "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector');" 2>/dev/null || echo "f")

    if [[ "$ext_check" =~ "t" ]]; then
        print_status "success" "pgvector extension already installed"
    else
        # Install the extension
        psql -h $DB_HOST -p $DB_PORT -d $DB_NAME -c "CREATE EXTENSION vector;"
        print_status "success" "pgvector extension installed"
    fi
}

test_vector_operations() {
    print_status "info" "Testing vector operations..."

    # Test basic vector creation and operations
    local test_result
    test_result=$(psql -h $DB_HOST -p $DB_PORT -d $DB_NAME -t -c "SELECT '[1,2,3]'::vector(3) <-> '[4,5,6]'::vector(3);" 2>/dev/null || echo "error")

    if [[ "$test_result" != "error" ]]; then
        print_status "success" "Vector operations working correctly"
        print_status "info" "Distance calculation result: $test_result"
    else
        print_status "error" "Vector operations failed"
        return 1
    fi
}

main() {
    print_header

    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --db-name=*)
                DB_NAME="${1#*=}"
                shift
                ;;
            --db-host=*)
                DB_HOST="${1#*=}"
                shift
                ;;
            --db-port=*)
                DB_PORT="${1#*=}"
                shift
                ;;
            --db-user=*)
                DB_USER="${1#*=}"
                shift
                ;;
            --help)
                echo "Usage: $0 [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --db-name=NAME     Database name (default: superschedules)"
                echo "  --db-host=HOST     Database host (default: localhost)"
                echo "  --db-port=PORT     Database port (default: 5432)"
                echo "  --db-user=USER     Database user (default: current user)"
                echo "  --help             Show this help message"
                exit 0
                ;;
            *)
                print_status "error" "Unknown option: $1"
                exit 1
                ;;
        esac
    done

    print_status "info" "Configuration:"
    print_status "info" "  Database: $DB_NAME"
    print_status "info" "  Host: $DB_HOST:$DB_PORT"
    print_status "info" "  User: $DB_USER"
    echo ""

    # Step 1: Check PostgreSQL
    if ! check_postgresql; then
        exit 1
    fi

    # Step 2: Install pgvector
    install_pgvector

    # Step 3: Restart PostgreSQL to load the extension
    print_status "info" "Restarting PostgreSQL to load pgvector..."
    sudo systemctl restart postgresql || print_status "warning" "Could not restart PostgreSQL - you may need to restart manually"

    # Wait for PostgreSQL to come back up
    sleep 3

    if ! check_postgresql; then
        print_status "error" "PostgreSQL failed to restart"
        exit 1
    fi

    # Step 4: Create database if needed
    create_database

    # Step 5: Install extension
    install_extension

    # Step 6: Test vector operations
    test_vector_operations

    echo ""
    print_status "success" "pgvector installation complete!"
    echo -e "${BLUE}💡 You can now run your Django migrations and use vector operations${NC}"
}

main "$@"