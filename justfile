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

# Install the qseek checkout into its .venv; run again after changing C extensions
setup:
    uv sync --project {{ quote(qseek_dir) }} --inexact --reinstall-package qseek
    {{ uv_run }} qseek --version

# Download the waveforms and station metadata of an example
download example="campi-flegrei":
    cd "$1" && uvx fdsn-rush download download.json

# Download the reference catalog of an example again
catalog example="campi-flegrei":
    {{ playground }} catalog "$@"

# Write the search configuration of a run to <example>/runs/<run>.json
config example="campi-flegrei" run="dev" *options:
    {{ playground }} config "$@"

# Run a quiet search (errors only, no live view) and extract its metrics; options: --set key.path=value, --config, --force
search example="campi-flegrei" run="dev" *options:
    {{ playground }} search "$@"

# Run a search with source-specific station corrections from a previous run
ssst example="campi-flegrei" run="ssst" from="dev" *options:
    {{ playground }} search "$1" "$2" --ssst-from "$3" "${@:4}"

# Extract the metrics of a finished run again
metrics example="campi-flegrei" run="dev":
    {{ playground }} metrics "$@"

# Compare a run with the baseline or another run; fails on a regression
compare example="campi-flegrei" run="dev" against="baseline":
    {{ playground }} compare "$@"

# Make the metrics of a run the baseline of the example
bless example="campi-flegrei" run="dev":
    {{ playground }} bless "$@"

# List the runs of an example with their key metrics
runs example="campi-flegrei":
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
    [[ "$2" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && "$2" != baseline ]] || { echo "invalid run name $2" >&2; exit 1; }
    rm -rf "$1/runs/$2" "$1/runs/$2.json"

# Serve the dashboard to look at and compare the runs of the examples
dashboard port="2214":
    {{ playground }} dashboard --port "$1"

# Relocate the detections of a run with HypoDD into <run>-hypodd; options: --cc, --set key.path=value, --force
hypodd example="campi-flegrei" from="dev" *options:
    {{ uv_run }} python {{ quote(justfile_directory() / "scripts" / "hypodd.py") }} relocate "$@"

# Compare a HypoDD run with its source run and other runs on their common events
hypodd-compare example="campi-flegrei" run="dev-hypodd" *runs:
    {{ uv_run }} python {{ quote(justfile_directory() / "scripts" / "hypodd.py") }} compare "$@"
