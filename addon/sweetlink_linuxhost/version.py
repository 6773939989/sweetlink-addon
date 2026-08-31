# SPDX-License-Identifier: AGPL-3.0-or-later
# Derivato dall'add-on Home Assistant di Homeway.io (https://github.com/homewayio/AddOn),
# a sua volta derivato da OctoEverywhere. Vedi NOTICE.md.

import os
import yaml

class Version:

    # Parses the common plugin version from the config.yaml.
    # Throws if the file can't be found or the version string can't be found.
    @staticmethod
    def GetPluginVersion(repoRoot:str) -> str:
        # Use the dockerfile, so it's the source of truth.
        versionFilePath = os.path.join(repoRoot, "config.yaml")
        if os.path.exists(versionFilePath) is False:
            raise Exception("Failed to find our repo root setup file to parse the version. Expected Path: "+versionFilePath)

        # Read the file, find the version string.
        with open(versionFilePath, "r", encoding="utf-8") as f:
            parsedYaml = yaml.safe_load(f)
            if "version" not in parsedYaml:
                raise Exception(f"Version key in yaml file: {versionFilePath}")
            return parsedYaml["version"]
