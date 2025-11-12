#!/bin/sh
set -e

# Handle secret files or environment variables
export POSTGRES_PASSWORD=$(cat ${POSTGRES_PASSWORD_FILE:-/dev/null} 2>/dev/null || echo "$POSTGRES_PASSWORD")
export APP_KEY=$(cat ${APP_KEY_FILE:-/dev/null} 2>/dev/null || echo "$APP_KEY")

# Reassemble bootstrap file from parts at first startup (if not already done)
if [ -d "/app/data/parts" ]; then
    # Handle if bootstrap path exists as directory (from old volume mounts)
    if [ -e "/app/data/watchbuddy_bootstrap.tar.gz" ]; then
        if [ -d "/app/data/watchbuddy_bootstrap.tar.gz" ]; then
            echo "WARNING: Bootstrap path exists as directory (likely old mount point)"
            echo "Moving it out of the way..."
            mv /app/data/watchbuddy_bootstrap.tar.gz /app/data/watchbuddy_bootstrap.tar.gz.old 2>/dev/null || {
                echo "ERROR: Cannot move old bootstrap directory - it may be mounted"
                echo "Please stop containers and remove the watchbuddy_bootstrap volume manually"
                echo "Command: docker volume ls | grep bootstrap"
                exit 1
            }
        else
            echo "Bootstrap file already exists, skipping reassembly"
        fi
    fi
    
    if [ ! -f "/app/data/watchbuddy_bootstrap.tar.gz" ]; then
        echo "Reassembling bootstrap file from split parts..."
        cat /app/data/parts/watchbuddy_bootstrap.part.* > /app/data/watchbuddy_bootstrap.tar.gz
        echo "Bootstrap file reassembled successfully ($(du -h /app/data/watchbuddy_bootstrap.tar.gz | cut -f1))"
        rm -rf /app/data/parts/
        echo "Removed part files to save space"
    fi
fi

# Execute the main command
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 75 --timeout-graceful-shutdown 30 --workers 2
