#!/bin/bash

# Quick benchmark for optimal thread counts
echo "Quick benchmark for optimal thread counts..."
echo "========================================="

for threads in 8 12 16; do
    echo ""
    echo "Testing full suite with $threads threads..."
    start_time=$(date +%s)
    ./scripts/dev-test-all.sh --threads=$threads >/dev/null 2>&1
    end_time=$(date +%s)
    total_time=$((end_time - start_time))
    echo "  Full suite with $threads threads: ${total_time}s"
done