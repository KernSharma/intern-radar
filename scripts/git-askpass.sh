#!/bin/bash
# GIT_ASKPASS helper for the Mac runner: username x-access-token, password =
# the fine-grained PAT (this repo only; contents + issues) from the Keychain.
case "$1" in
  Username*) echo "x-access-token" ;;
  *) security find-generic-password -s radar-mac-pat -w ;;
esac
