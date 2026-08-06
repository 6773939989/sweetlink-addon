#!/bin/bash

cd "$(dirname "$0")/.."

echo "Linting..."
pylint --rcfile .pylintrc --output-format=colorized -j 8 addon