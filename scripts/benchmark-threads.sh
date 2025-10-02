#!/bin/bash

# Benchmark different thread counts
echo "Benchmarking pytest thread counts on 5800X3D..."
echo "=============================================="

for threads in 4 8 12 16 20; do
    echo ""
    echo "Testing with $threads threads..."

    start_time=$(date +%s)
    ./scripts/dev-test-all.sh --threads=$threads --repo=navigator >/dev/null 2>&1
    end_time=$(date +%s)

    total_time=$((end_time - start_time))
    echo "  Navigator with $threads threads: ${total_time}s"

    start_time=$(date +%s)
    ./scripts/dev-test-all.sh --threads=$threads --repo=collector >/dev/null 2>&1
    end_time=$(date +%s)

    total_time=$((end_time - start_time))
    echo "  Collector with $threads threads: ${total_time}s"
done

echo ""
echo "Testing full suite with optimal thread count..."
for threads in 8 12 16; do
    echo ""
    echo "Full suite with $threads threads..."
    start_time=$(date +%s)
    ./scripts/dev-test-all.sh --threads=$threads >/dev/null 2>&1
    end_time=$(date +%s)
    total_time=$((end_time - start_time))
    echo "  Full suite with $threads threads: ${total_time}s"
done