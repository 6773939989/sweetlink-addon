# SPDX-License-Identifier: AGPL-3.0-or-later
# Derivato dall'add-on Home Assistant di Homeway.io (https://github.com/homewayio/AddOn),
# a sua volta derivato da OctoEverywhere. Vedi NOTICE.md.
# Modificato da Sweetplace (M2R S.r.l.), 2026.

import os
import time
import json
import logging
import threading
from typing import Dict, List, Any, Optional

from sweetlink.httpsessions import HttpSessions
from sweetlink.interfaces import IConfigManager

from ..config import Config


# Helps manage WebRTC connections and related configurations.
class WebRtcManager():

    c_ConfigUsernameKey = "Username"
    c_ConfigPasswordKey = "Password"
    c_ConfigStunServersKey = "StunServers"
    c_ConfigTurnServersKey = "TurnServers"
    c_ConfigCacheTimeKey = "CacheTime"

    c_MaxCacheAgeSec = 604800  # 7 days


    def __init__(self, logger:logging.Logger, pluginId:str, pluginDataFolderPath:str, config:Config, haConfigManager:IConfigManager) -> None:
        self.Logger = logger
        self.PluginId = pluginId
        self.Config = config
        self.HaConfigManager = haConfigManager

        self.CacheLock = threading.Lock()
        self.CacheFilePath = os.path.join(pluginDataFolderPath, "webrtc_cache.json")


    def OnPrimaryConnectionEstablished(self, apiKey:str) -> None:
        # Start a background thread to update the cache if needed.
        threading.Thread(target=self._UpdateCacheIfNeeded, args=(apiKey,), daemon=True).start()


    def _UpdateCacheIfNeeded(self, apiKey:str) -> None:

        with self.CacheLock:
            try:
                # Load existing cache if it exists.
                existingCache:Dict[str, Any] = {}
                if os.path.exists(self.CacheFilePath):
                    with open(self.CacheFilePath, "r",encoding="utf-8") as f:
                        existingCache = json.load(f)

                # Validate the cache has all of tha values we need, otherwise we need to pull the API again.
                username = existingCache.get(self.c_ConfigUsernameKey, None)
                password = existingCache.get(self.c_ConfigPasswordKey, None)
                stunServers = existingCache.get(self.c_ConfigStunServersKey, None)
                turnServers = existingCache.get(self.c_ConfigTurnServersKey, None)
                cacheTime = existingCache.get(self.c_ConfigCacheTimeKey, 0)
                if (time.time() - cacheTime) < self.c_MaxCacheAgeSec and username is not None and password is not None and isinstance(stunServers, list) and isinstance(turnServers, list):
                    self.Logger.debug("WebRTC cache file is valid; no update needed.")
                    self._ProcessConfig(username, password, stunServers, turnServers)
                    return

                # Anche questa strada non parte piu': ci si arriva solo con la chiave che
                # dava l'handshake con il servizio di terzi, e quella connessione non c'e' piu'.
                # La configurazione WebRTC tornera' dal nostro TURN, non da qui.
                self.Logger.info("Fetching WebRTC configuration from server.")
                request = {
                    "PluginId": self.PluginId,
                    "ApiKey": apiKey
                }
                url = os.environ.get("HOMEWAY_URL", "https://homeway.io") + "/api/webrtc/config"
                result = HttpSessions.GetSession(url).post(url, json=request, timeout=15.0)
                if result.status_code != 200:
                    raise ValueError(f"Failed to get WebRTC config from server; status code: {result.status_code}")
                resultJson = result.json()
                resultObj = resultJson.get("Result", None)
                if resultObj is None:
                    raise ValueError("Invalid response from WebRTC config server; missing Result.")

                # Save the new cache file.
                with open(self.CacheFilePath, "w", encoding="utf-8") as f:
                    resultObj[self.c_ConfigCacheTimeKey] = time.time()
                    f.write(json.dumps(resultObj, indent=4))

                # Get the new values.
                username = resultObj.get("Username", "")
                password = resultObj.get("Password", "")
                stunServers = resultObj.get("StunServers", [])
                turnServers = resultObj.get("TurnServers", [])
                self._ProcessConfig(username, password, stunServers, turnServers)

            except Exception as e:
                self.Logger.error(f"Error updating WebRTC cache file: {e}")


    def _ProcessConfig(self, username:str, password:str, stunServers:List[str], turnServers:List[str]) -> None:

        # Write the values into the config so the user can find them easily.
        self.Config.SetStr(Config.WebRtcSection, Config.WebRtcUsernameKey, username)
        self.Config.SetStr(Config.WebRtcSection, Config.WebRtcPasswordKey, password)
        self.Config.SetStr(Config.WebRtcSection, Config.WebRtcStunServersKey, json.dumps(stunServers))
        self.Config.SetStr(Config.WebRtcSection, Config.WebRtcTurnServersKey, json.dumps(turnServers))

        if not self.HaConfigManager.CanEditConfig():
            self.Logger.info("WebRTC enables secure and low-latency remote camera and video streaming.\nYour WebRTC username and password are:\nUsername: %s\nPassword: %s\n\n", username, password)
            return

        # Try to update the Home Assistant config file with web_rtc settings
        # Uses the HA web_rtc integration format: https://www.home-assistant.io/integrations/web_rtc/
        self._UpdateWebRtcConfig(username, password, stunServers, turnServers)


    # Comment marker to identify Sweetplace-managed web_rtc config sections
    # Flag keyword that users can set to false in the comment to stop Sweetplace from auto-updating
    # This must remain as a one line comment!
    c_ConfigLineEnding       = "\r\n"
    c_SweetplaceAutoUpdateFlag  = "sweetplace_auto_update"
    c_SweetplaceCommentMarker   = "# Added by Sweetplace"
    # Marcatore usato dalle versioni precedenti al cambio di nome. Va riconosciuto ancora,
    # altrimenti su un hub che ha gia' quel commento nel proprio configuration.yaml la sezione
    # web_rtc risulterebbe scritta a mano dall'utente e verrebbe congelata (Case 2 qui sotto),
    # cioe' non aggiorneremmo piu' STUN e TURN su quel dispositivo.
    # Si potra' togliere quando nessun hub in circolazione porta piu' il marcatore vecchio.
    c_LegacyCommentMarker       = "# Added by Homeway"
    c_LegacyAutoUpdateFlag      = "homeway_auto_update"
    c_SweetplaceCommentFullLine = f"{c_SweetplaceCommentMarker} to enable webrtc video streaming. Prevent Sweetplace from updating web_rtc by setting following to false: {c_SweetplaceAutoUpdateFlag}:true{c_ConfigLineEnding}" # this ending must remain "c_SweetplaceAutoUpdateFlag:true"

    def _UpdateWebRtcConfig(self, username:str, password:str, stunServers:List[str], turnServers:List[str]) -> None:
        try:
            # Behavior
            #  - If there is no web_rtc section in the config, it will be added with a comment that it's from Sweetplace.
            #  - If there is a web_rtc section.
            #       - If the section was generated by Sweetplace, and the auto update flag is true, update it with the new values.
            #       - If the section was created by the user, add the sweetplace comment to allow the user to set auto update to true, but don't mess with that's currently there.

            # Get the config file path.
            configFilePath = self.HaConfigManager.GetConfigFilePath()
            if configFilePath is None:
                self.Logger.warning("WebRTC: Cannot update web_rtc config - config file path not found.")
                return

            # Read the entire file.
            lines:List[str] = []
            with open(configFilePath, 'r', encoding="utf-8") as f:
                lines = f.readlines()

            # First, check if there is an existing webrtc section and sweetplace comment.
            webRtcSectionLineNumber:Optional[int] = None
            sweetplaceCommentLineNumber:Optional[int] = None
            autoUpdateEnabled:bool = True
            lineNumber = 0
            while lineNumber < len(lines):
                line = lines[lineNumber]
                lineLower = line.lower()

                # Look for web_rtc section (the HA integration key)
                if lineLower.startswith("web_rtc:"):
                    self.Logger.debug("WebRTC: Found existing web_rtc config section at line %d.", lineNumber + 1)
                    webRtcSectionLineNumber = lineNumber
                    # Check the lines immediately before web_rtc for Sweetplace comment.
                    # The Sweetplace comment will always be one line.
                    checkLine = lineNumber - 1
                    while checkLine >= 0:
                        checkLineLower = lines[checkLine].lower()
                        # Only check comment lines.
                        if not checkLineLower.strip().startswith("#"):
                            break
                        if self.c_SweetplaceCommentMarker.lower() in checkLineLower or self.c_LegacyCommentMarker.lower() in checkLineLower:
                            sweetplaceCommentLineNumber = checkLine
                            self.Logger.debug("WebRTC: Found Sweetplace comment marker at line %d.", checkLine + 1)
                            # Check the value of the auto_update flag.
                            flagName = self.c_SweetplaceAutoUpdateFlag
                            flagPosition = checkLineLower.find(flagName.lower())
                            if flagPosition == -1:
                                # Riga scritta da una versione precedente al cambio di nome.
                                flagName = self.c_LegacyAutoUpdateFlag
                                flagPosition = checkLineLower.find(flagName.lower())
                            if flagPosition != -1:
                                flagPosition += len(flagName)
                                # Get the rest of the line after the flag, check if the value false is in there.
                                flagValuePartLower = checkLineLower[flagPosition:]
                                self.Logger.debug(f"WebRTC: Found auto update flag in Sweetplace comment. `{flagValuePartLower}`")
                                if "false" in flagValuePartLower:
                                    autoUpdateEnabled = False
                        break
                    break  # Stop searching after finding web_rtc section
                lineNumber += 1

            # Case 1: No web_rtc section exists - add new one with Sweetplace comment
            if webRtcSectionLineNumber is None:
                self.Logger.info("WebRTC: Adding new web_rtc config section to Home Assistant configuration.")
                webRtcConfig = self._BuildWebRtcConfig(username, password, stunServers, turnServers)
                with open(configFilePath, 'a', encoding="utf-8") as f:
                    f.write(self.c_ConfigLineEnding)
                    f.write(self.c_SweetplaceCommentFullLine)
                    f.write(webRtcConfig)
                    f.write(self.c_ConfigLineEnding)
                return

            # Case 2: web_rtc section exists but no Sweetplace comment - add comment with auto_update=false to preserve user config
            if sweetplaceCommentLineNumber is None:
                self.Logger.info("WebRTC: Found existing web_rtc config without Sweetplace marker. Adding marker with auto_update=false to preserve user config.")
                # Insert the comment line before the web_rtc section
                fullCommentLine = self.c_SweetplaceCommentFullLine
                # Replace the c_SweetplaceAutoUpdateFlag to set it to false
                fullCommentLine = fullCommentLine.replace(f"{self.c_SweetplaceAutoUpdateFlag}:true", f"{self.c_SweetplaceAutoUpdateFlag}:false")
                lines.insert(webRtcSectionLineNumber, fullCommentLine)
                with open(configFilePath, 'w', encoding="utf-8") as f:
                    f.writelines(lines)
                return

            # Case 3: web_rtc section exists with Sweetplace comment but auto_update=false
            if not autoUpdateEnabled:
                self.Logger.debug("WebRTC: web_rtc config exists with auto_update=false. Skipping update.")
                return

            # Case 4: web_rtc section exists with Sweetplace comment and auto_update is not disabled - update the config
            self.Logger.debug("WebRTC: Updating existing web_rtc config section.")
            self._ReplaceWebRtcSection(lines, webRtcSectionLineNumber, sweetplaceCommentLineNumber, username, password, stunServers, turnServers, configFilePath)

        except Exception as e:
            self.Logger.error(f"WebRTC: Error updating web_rtc config: {e}")


    def _BuildWebRtcConfig(self, username:str, password:str, stunServers:List[str], turnServers:List[str]) -> str:
        """Build the web_rtc YAML config content following HA web_rtc integration format.
        See: https://www.home-assistant.io/integrations/web_rtc/
        """
        config = f"web_rtc:{self.c_ConfigLineEnding}"
        config += f"  ice_servers:{self.c_ConfigLineEnding}"

        # Add STUN servers as a single entry with multiple URLs
        if len(stunServers) > 0:
            config += f"    - url:{self.c_ConfigLineEnding}"
            for server in stunServers:
                config += f"        - \"{server}\"{self.c_ConfigLineEnding}"

        # Add TURN servers as a single entry with multiple URLs sharing the same credentials
        if len(turnServers) > 0:
            config += f"    - url:{self.c_ConfigLineEnding}"
            for server in turnServers:
                config += f"        - \"{server}\"{self.c_ConfigLineEnding}"
            config += f"      username: \"{username}\"{self.c_ConfigLineEnding}"
            config += f"      credential: \"{password}\"{self.c_ConfigLineEnding}"

        return config


    def _ReplaceWebRtcSection(self, lines:List[str], webRtcLineNumber:int, commentLineNumber:int, username:str, password:str, stunServers:List[str], turnServers:List[str], configFilePath:str) -> None:
        # Start from the comment line number, which is before the web_rtc section
        if commentLineNumber >= webRtcLineNumber:
            raise ValueError("Comment line number must be before web_rtc section line number.")
        if commentLineNumber + 1 != webRtcLineNumber:
            raise ValueError("Comment line must be immediately before web_rtc section line.")

        # Find the end of the web_rtc section (next top-level key or EOF)
        endLineNumber = webRtcLineNumber + 1
        while endLineNumber < len(lines):
            line = lines[endLineNumber]
            # If line starts with a non-space character and is not empty/comment, it's a new section
            if len(line) > 0 and (line[0].isalnum() or line.startswith("#")):
                break
            endLineNumber += 1

        # Build the new config section
        newConfig = self._BuildWebRtcConfig(username, password, stunServers, turnServers)

        # Remove old lines
        del lines[commentLineNumber:endLineNumber]

        # Insert new content
        lines.insert(commentLineNumber, self.c_SweetplaceCommentFullLine + newConfig)

        # Write back
        with open(configFilePath, 'w', encoding="utf-8") as f:
            f.writelines(lines)
