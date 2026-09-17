#!/usr/bin/env bash
# Read the discovery document the domain currently serves, for the release
# workflow, and say which of three things happened:
#
#   exit 0, OUT written    versions.json was read and is a well-formed document
#   exit 0, OUT absent     the object is confirmed missing (a 404 from the
#                          bucket) AND no other release root is marked
#                          RELEASED: the first release ever
#   exit 1                 anything else: a network, authentication or
#                          service failure, a malformed document, or a
#                          missing document beside released roots
#
# A failed read used to become an empty file, which the generator took for
# "no releases yet" - and a rerun of an old tag would then have published a
# document listing only that tag and moved the alias backwards. Now an
# unexpected failure stops the run before discovery or the alias changes.
#
#   scripts/fetch_versions.sh BUCKET TAG OUT
set -uo pipefail
bucket=$1; tag=$2; out=$3
err=$(mktemp)
rm -f "$out"

if aws s3 cp "s3://$bucket/versions.json" "$out" > /dev/null 2> "$err"; then
  if python -m registry.versions --validate "$out"; then
    echo "read versions.json from the bucket"
    exit 0
  fi
  echo "::error::versions.json was downloaded but is not a valid discovery document"
  exit 1
fi

if grep -qE '\(404\)|NoSuchKey|Not Found' "$err"; then
  # Confirmed missing. Allowed only when nothing has been released yet:
  # a released root beside a missing versions.json means the document
  # was lost, not never written, and a fresh one would forget those roots.
  if ! listing=$(aws s3 ls "s3://$bucket/" 2>> "$err"); then
    cat "$err"; echo "::error::versions.json is missing and the bucket could not be listed"; exit 1
  fi
  for prefix in $(printf '%s\n' "$listing" | awk '/PRE v[0-9]/ {print $2}'); do
    [ "$prefix" = "$tag/" ] && continue
    if aws s3 ls "s3://$bucket/${prefix}RELEASED" > /dev/null 2>&1; then
      echo "::error::versions.json is missing but ${prefix}RELEASED exists; refusing to start discovery over"; exit 1
    fi
  done
  echo "no versions.json in the bucket: first release"
  exit 0
fi

cat "$err"
echo "::error::could not read versions.json (not a 404); stopping before discovery or the alias changes"
exit 1
