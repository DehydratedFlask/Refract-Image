#!/usr/bin/env bash
# Double-click this file in Finder to set Refract Image up.
#
# It runs scripts/setup.sh, which checks the machine, installs the Python runtime (mflux on
# MLX), the app's dependencies, and downloads + stages the ~14 GB 4-bit model. Re-running is
# safe: finished steps are skipped.
#
# It also builds ./Refract Image.app at the end, so there is something to double-click once
# the window closes. Pass --skip-model or --yes after this file's name in Terminal to change
# what setup does; a double-click passes nothing.
#
# Nothing here needs a terminal, but macOS runs a .command file in Terminal so you can
# watch progress and see the reason if a step fails.
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

echo "Refract Image setup"
echo "This downloads about 14 GB and writes a ~11 GB model, so it can take a while."
echo

./scripts/setup.sh --native "$@"
status=$?

echo
if [[ $status -eq 0 ]]; then
  echo "Setup finished. You can close this window."
  echo "To start the app, double-click Refract Image.app in this folder,"
  echo "or run it from here: open \"Refract Image.app\""
else
  echo "Setup failed (exit $status). The messages above explain why — fix that and run this file again."
fi
echo
echo "Press Return to close this window."
read -r _ || true
exit $status
