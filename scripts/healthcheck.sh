#!/usr/bin/env sh
set -eu
curl --fail --silent --show-error --max-time 3 http://127.0.0.1:8080/api/health >/dev/null
