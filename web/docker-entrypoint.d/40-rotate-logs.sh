#!/bin/sh
# Start the loop that keeps nginx's two log files bounded. See logrotate.conf.
#
# The stock entrypoint runs everything in /docker-entrypoint.d/ and then `exec`s nginx,
# so a process backgrounded here is reparented to nginx as PID 1 and outlives this
# script. That is the whole trick, and it is also this file's one weakness: nothing
# supervises the loop, so if it dies the files grow again and nothing says so. The
# alternatives are s6 or supervisord in an image whose entire job is serving static
# files, which is a large amount of machinery for two log files. If rotation ever does
# stop, `ls -la logs/` is where it shows.
#
# `size` is the only condition in the config, so this interval is just how often the
# question gets asked — not how often anything rotates. Overridable so that a test does
# not have to wait ten minutes to find out whether any of this works.

set -eu

INTERVAL="${TIMOTHY_LOG_ROTATE_INTERVAL:-600}"

if [ ! -d /logs ]; then
    echo "$0: /logs is not mounted; nginx's logs will not be rotated" >&2
    exit 0
fi

# Subshell in the background, detached from this script's exit.
(
    while true; do
        sleep "$INTERVAL"
        # `|| true`: a rotation that fails must not kill the loop that would have
        # retried it ten minutes later.
        /usr/sbin/logrotate --state /tmp/logrotate.state /etc/logrotate.d/timothy || true
    done
) &

echo "$0: rotating nginx logs every ${INTERVAL}s"
