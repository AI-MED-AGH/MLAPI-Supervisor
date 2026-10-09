#!/bin/bash

# It is a script that post a bunch of subscriptions. Meant to be used for testing docker.

curl -H "X-Admin-Key: $ADMIN_KEY" -X POST "http://localhost:8000/v1/observers/" \
    -H "Content-Type: application/json" \
    -d '{"webhook_url": "https://your-server.com/webhooks/orders", "event_types": ["error"]}' \

echo ""

curl -H "X-Admin-Key: $ADMIN_KEY" -X POST "http://localhost:8000/v1/observers/" \
    -H "Content-Type: application/json" \
    -d '{"webhook_urls": "https://your-server.com/webhooks/orders", "event_types": ["error"]}' \

echo ""

curl -H "X-Admin-Key: $ADMIN_KEY" -X POST "http://localhost:8000/v1/observers/" \
    -H "Content-Type: application/json" \
    -d '{"webhook_url": "https://your-server.com/webhooks/orders", "event_types": ["a", "b", "c"]}' \

echo ""
# Test for SQL Injection vulnerability
curl -H "X-Admin-Key: $ADMIN_KEY" -X POST "http://localhost:8000/v1/observers/" \
    -H "Content-Type: application/json" \
    -d '{"webhook_url": "https://your-server.com/webhooks/orders", "event_types": ["order.shipped '\''); DROP TABLE observers; --"]}' \

echo ""
# if SQL succeeded than this POST should fail, since there is no longer observers table
curl -H "X-Admin-Key: $ADMIN_KEY" -X POST "http://localhost:8000/v1/observers/" \
    -H "Content-Type: application/json" \
    -d '{"webhook_url": "https://your-server.com/webhooks/orders", "event_types": ["d"]}' \
