#!/usr/bin/env bash
# Provisions (or updates) creative-strategist on a single Amazon Linux 2023 EC2 box.
# Idempotent: run it again after a `git pull` to deploy a new version -- it
# reinstalls deps, rebuilds, re-migrates, and restarts all four services.
#
# What this does NOT do: it never writes secrets. Copy real values from your
# local .env / web/.env.local, but point every URL at THIS box's public IP or
# domain instead of localhost. Two files must exist before the FIRST run:
#   /etc/creative-strategist/backend.env   -- everything in .env.example (api/worker/scheduler)
#   /etc/creative-strategist/web.env       -- CLERK_SECRET_KEY (the Next.js SERVER reads this
#                                              at runtime; NEXT_PUBLIC_* vars do not belong here)
# A third only exists inside the git checkout this script itself creates, so
# it can't be created ahead of time -- this run will clone the repo, then stop
# and tell you to fill it in before running again:
#   /opt/creative-strategist/web/.env.local -- NEXT_PUBLIC_API_URL, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
#                                               (baked into the client bundle at build time)
set -euo pipefail

REPO_URL="https://github.com/marketing-brain/creative-strategist.git"
APP_DIR="/opt/creative-strategist"
BACKEND_ENV="/etc/creative-strategist/backend.env"
WEB_ENV="/etc/creative-strategist/web.env"
WEB_ENV_LOCAL="$APP_DIR/web/.env.local"

for f in "$BACKEND_ENV" "$WEB_ENV"; do
  if [ ! -f "$f" ]; then
    echo "Missing $f -- create it first (see the comment at the top of this script), then re-run." >&2
    exit 1
  fi
done
# web/.env.local lives inside the git checkout itself, which doesn't exist yet
# on a fresh box -- checked further down, right after cloning creates the
# directory it belongs in (checking here, before the clone, would mean the
# operator has to pre-create web/.env.local in an empty $APP_DIR, which then
# makes `git clone` refuse to clone into a now-non-empty directory).

# --- system deps (idempotent: dnf/curl steps no-op if already present) ---
if ! id -u cs >/dev/null 2>&1; then
  sudo useradd --system --create-home --shell /usr/sbin/nologin cs
fi

sudo dnf update -y
# curl deliberately omitted: AL2023 ships curl-minimal preinstalled, which
# conflicts with the full curl package (both provide the same files) and
# makes dnf refuse the whole install. curl-minimal already covers everything
# this script uses (curl -fsSL / -o against plain HTTPS URLs).
sudo dnf install -y git ca-certificates gcc gcc-c++ make tar xz

if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo -E bash -
  sudo dnf install -y nodejs
fi

if ! command -v dbmate >/dev/null 2>&1; then
  sudo curl -fsSL -o /usr/local/bin/dbmate \
    https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64
  sudo chmod +x /usr/local/bin/dbmate
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  # Not in AL2023's dnf repos at all (patent-encumbered codecs) -- a static
  # build sidesteps that entirely, same pattern as the dbmate binary above.
  tmp_ffmpeg="$(mktemp -d)"
  curl -fsSL -o "$tmp_ffmpeg/ffmpeg.tar.xz" \
    https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
  tar -xf "$tmp_ffmpeg/ffmpeg.tar.xz" -C "$tmp_ffmpeg" --strip-components=1
  sudo install -m 755 "$tmp_ffmpeg/ffmpeg" "$tmp_ffmpeg/ffprobe" /usr/local/bin/
  rm -rf "$tmp_ffmpeg"
fi

if ! command -v uv >/dev/null 2>&1; then
  # Installed straight to /usr/local/bin (already on every user's PATH,
  # including the `cs` service user's) rather than the invoking user's
  # $HOME/.local/bin -- avoids a symlink into a home directory the `cs`
  # user may not be able to traverse.
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi

# --- fetch / update the app ---
if [ -d "$APP_DIR/.git" ]; then
  sudo -u cs git -C "$APP_DIR" pull --ff-only
else
  sudo mkdir -p "$APP_DIR"
  sudo chown cs:cs "$APP_DIR"
  sudo -u cs git clone "$REPO_URL" "$APP_DIR"
fi

if [ ! -f "$WEB_ENV_LOCAL" ]; then
  echo "Missing $WEB_ENV_LOCAL -- create it (NEXT_PUBLIC_API_URL, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY), then re-run." >&2
  exit 1
fi

# --- backend: install deps, migrate ---
cd "$APP_DIR"
sudo -u cs uv sync --frozen --no-dev
set -a
# shellcheck disable=SC1090
source "$BACKEND_ENV"
set +a
dbmate --no-dump-schema --migrations-dir db/migrations up

# --- frontend: install deps, build (bakes web/.env.local's NEXT_PUBLIC_* vars in) ---
cd "$APP_DIR/web"
sudo -u cs npm install
sudo -u cs npm run build

# --- systemd: install unit files, (re)start everything ---
sudo cp "$APP_DIR/deploy/ec2/systemd/"*.service /etc/systemd/system/
sudo systemctl daemon-reload
for svc in cs-api cs-worker cs-scheduler cs-web; do
  sudo systemctl enable "$svc"
  sudo systemctl restart "$svc"
done

echo "Done. Check status with: sudo systemctl status cs-api cs-worker cs-scheduler cs-web"
