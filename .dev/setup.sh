#!/bin/bash

echo "Installing Required Libs"
sudo apt-get update
sudo apt-get install python3 -y
sudo apt-get install python3-pip -y

echo "Installing Python Libs"
pip install -r ../addon/requirements.txt

# Use sudo, so it installs globally
echo "Installing pylint"
sudo pip install pylint 1> /dev/null 2> /dev/null

echo "Done. Debug configs are in .vscode/launch.json"
