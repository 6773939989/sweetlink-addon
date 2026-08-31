# SPDX-License-Identifier: AGPL-3.0-or-later
# Derivato dall'add-on Home Assistant di Homeway.io (https://github.com/homewayio/AddOn),
# a sua volta derivato da OctoEverywhere. Vedi NOTICE.md.

from pathlib import Path

class Util:

    @staticmethod
    def EnsureDirExists(path:str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)


    @staticmethod
    def IsStrNullOrWhitespace(s:str) -> bool:
        return s is None or (isinstance(s, str) and s.strip() == "")
