"""
Settings — client.core.config.__init__ ke Settings class ka re-export.

NOTE: Pehle yaha ek ALAG (aur buggy) Settings class thi jo apna khud ka
.env loading logic rakhti thi — usme base_path galat resolve hota tha
(client/core/config/ ki jagah client/ tak jaana chahiye tha), jisse
SCREENSHOT_ENCRYPTION_KEY jaise vars silently missing rehte the.

Wo bug __init__.py mein already fix ho chuka hai. Is file ko duplicate
(aur potentially phir se out-of-sync) rakhne ki jagah, yahan sirf wahi
(already-correct) Settings class re-export karte hain — taaki
`from client.core.config.settings import Settings` import path bhi
kaam kare, bina kisi doosri jagah wahi buggy logic dobara likhe.
"""

from client.core.config import Settings

__all__ = ["Settings"]
