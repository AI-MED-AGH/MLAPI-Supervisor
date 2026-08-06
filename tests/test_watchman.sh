#!/bin/bash

# It is a script that post a bunch of subscriptions. Meant to be used for testing docker.

curl -X POST "http://localhost:8000/observers/" \
    -H "Content-Type: application/json" \
    -d '{"webhook_url": "https://your-server.com/webhooks/orders", "event_types": ["error"]}' \

echo ""

curl -X POST "http://localhost:8000/observers/" \
    -H "Content-Type: application/json" \
    -d '{"webhook_urls": "https://your-server.com/webhooks/orders", "event_types": ["error"]}' \

echo ""

curl -X POST "http://localhost:8000/observers/" \
    -H "Content-Type: application/json" \
    -d '{"webhook_url": "https://your-server.com/webhooks/orders", "event_types": ["a", "b", "c"]}' \

echo ""
# Test for SQL Injection vulnerability
curl -X POST "http://localhost:8000/observers/" \
    -H "Content-Type: application/json" \
    -d '{"webhook_url": "https://your-server.com/webhooks/orders", "event_types": ["order.shipped '\''); DROP TABLE observers; --"]}' \

echo ""
# if SQL succeeded than this POST should fail, since there is no longer observers table
curl -X POST "http://localhost:8000/observers/" \
    -H "Content-Type: application/json" \
    -d '{"webhook_url": "https://your-server.com/webhooks/orders", "event_types": ["d"]}' \
