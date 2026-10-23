#!/usr/bin/env bash
# Install the latest Spotify Soloist build and restart the service. Builds expire 90 days after
# their build date (Jarvis warns in Discord before that), so run this every couple of months.
set -euo pipefail
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl --fail --location -s -o "$tmp/soloist.tar.gz" https://soloist-builds.spotifycdn.com/soloist_release_x86_64.tar.gz
tar -xzf "$tmp/soloist.tar.gz" -C "$tmp"
version=$("$tmp/soloist" --version | awk '{print $2}')
dest=~/.local/opt/soloist-$version
mkdir -p "$dest"
cp "$tmp"/{soloist,CHANGELOG.md,THIRD_PARTY_LICENSES.txt} "$dest/"
ln -sfn "$dest/soloist" ~/.local/bin/soloist
soloist --version
systemctl --user restart soloist
