# Run the qseek playground examples with the local qseek checkout and measure the results.

set positional-arguments := true
set quiet := true
set shell := ["bash", "-euo", "pipefail", "-c"]

# qseek checkout whose .venv runs the examples
qseek_dir := env("QSEEK_DIR", justfile_directory() / ".." / "qseek")
uv_run := "uv run --project " + quote(qseek_dir) + " --no-sync"
playground := uv_run + " python " + quote(justfile_directory() / "scripts" / "playground.py")

# List the recipes
default:
    @just --list

# Install the qseek checkout and FDSN Rush into its .venv; run again after changing C extensions
setup:
    uv sync --project {{ quote(qseek_dir) }} --inexact --reinstall-package qseek
    uv pip install --python {{ quote(qseek_dir / ".venv") }} "fdsn-rush>=0.2"
    {{ uv_run }} qseek --version
    {{ uv_run }} fdsn-rush --version

# Download the waveforms and station metadata of an example; options: -n (non-interactive), -v
download example="campi-flegrei" *options:
    cd "$1" && {{ uv_run }} fdsn-rush download "${@:2}" download.json

# Download the reference catalog of an example again
catalog example="campi-flegrei":
    {{ playground }} catalog "$@"

# Write the search configuration of a run to <example>/runs/<run>.json
config example="campi-flegrei" run="dev" *options:
    {{ playground }} config "$@"

# Run a non-interactive search (errors and a few status lines only, no live view), print its metrics in one line and write metrics.json; options: --verbose, --set key.path=value, --config, --force
search example="campi-flegrei" run="dev" *options:
    {{ playground }} search "$@"

# Run one search per value of --vary key.path=v1,v2 (several --vary: all combinations), then list them; runs are named <run>-<key><value>
sweep example="campi-flegrei" run="sweep" *options:
    {{ playground }} sweep "$@"

# Run a search with source-specific station corrections from a previous run
ssst example="campi-flegrei" run="ssst" from="dev" *options:
    {{ playground }} search "$1" "$2" --ssst-from "$3" "${@:4}"

# Extract the metrics of a finished run again
metrics example="campi-flegrei" run="dev":
    {{ playground }} metrics "$@"

# Compare a run with another run, changed rows only (--full: all); fails on a regression
compare example run against *options:
    {{ playground }} compare "$@"

# List the runs of an example with their key metrics; --against RUN pairs them with a run
runs example="campi-flegrei" *options:
    {{ playground }} runs "$@"

# Open a run in the qseek web UI
explore example="campi-flegrei" run="dev":
    cd "$1" && {{ uv_run }} qseek explore "runs/$2"

# Open a run in Pyrocko Snuffler, with the observed and modeled arrivals
snuffler example="campi-flegrei" run="dev":
    cd "$1" && {{ uv_run }} qseek snuffler "runs/$2" --show-observed --show-modelled

# Delete a run
remove example run:
    [[ -f "$1/example.toml" ]] || { echo "no example $1" >&2; exit 1; }
    [[ "$2" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "invalid run name $2" >&2; exit 1; }
    rm -rf "$1/runs/$2" "$1/runs/$2.json"

# Delete all runs of an example
clear example:
    [[ -f "$1/example.toml" ]] || { echo "no example $1" >&2; exit 1; }
    shopt -s nullglob dotglob; runs=("$1"/runs/*); (( ${#runs[@]} )) && rm -rf -- "${runs[@]}"; echo "removed ${#runs[@]} entries from $1/runs"

# Serve the dashboard to look at and compare the runs of the examples
dashboard port="2214":
    {{ playground }} dashboard --port "$1"

# Relocate the detections of a run with HypoDD into <run>-hypodd; options: --cc, --set key.path=value, --force
hypodd example="campi-flegrei" from="dev" *options:
    {{ uv_run }} python {{ quote(justfile_directory() / "scripts" / "hypodd.py") }} relocate "$@"

# Compare a HypoDD run with its source run and other runs on their common events
hypodd-compare example="campi-flegrei" run="dev-hypodd" *runs:
    {{ uv_run }} python {{ quote(justfile_directory() / "scripts" / "hypodd.py") }} compare "$@"
