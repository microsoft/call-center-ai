#!/bin/bash
# Quick test script for Jay's Frames AI Assistant

echo "🎨 Jay's Frames AI Assistant - Local Test"
echo "=========================================="
echo ""

# Check if server is running
echo "Testing health endpoint..."
response=$(curl -s http://localhost:8080/health/liveness 2>/dev/null)

if [ $? -eq 0 ]; then
    echo "✅ Server is running!"
    echo "Response: $response"
else
    echo "❌ Server is not running"
    echo "Start it with: python -m uvicorn app.main:app --reload --port 8080"
    exit 1
fi

echo ""
echo "Available endpoints:"
echo "  http://localhost:8080              - Homepage"
echo "  http://localhost:8080/health/liveness - Health check"
echo "  http://localhost:8080/health/readiness - Readiness check"
echo "  http://localhost:8080/docs         - API documentation"
echo ""
echo "✅ Everything looks good!"
