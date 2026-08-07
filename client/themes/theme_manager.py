"""
The colours BaseWindow uses — now taken from the one palette everybody shares.

This used to be a THIRD colour system, alongside the employee panel's `C` and
the admin console's dict, with its own light/dark idea driven by a separate
"theme" setting. So the login window, the logs window and the screenshot
preview could disagree with the panels about what theme the application was
in, and the switch in either panel would not move them at all.

Everything here now delegates to client.presentation.theme. The class is kept
because BaseWindow and its three windows call it by name; it is a thin
adapter, not a palette.
"""

from client.presentation.theme import C, current_theme


class ThemeManager:

    @staticmethod
    def current() -> str:
        # Capitalised for the sake of anything that compared against "Dark".
        return current_theme().capitalize()

    @staticmethod
    def dark() -> bool:
        return current_theme() == "dark"

    @staticmethod
    def background() -> str:
        return C.BG

    @staticmethod
    def card() -> str:
        return C.CARD

    @staticmethod
    def border() -> str:
        return C.BORDER

    @staticmethod
    def primary_text() -> str:
        return C.TEXT

    @staticmethod
    def secondary_text() -> str:
        return C.TEXT_MUTED

    @staticmethod
    def accent() -> str:
        return C.PRIMARY

    @staticmethod
    def elevated() -> str:
        return C.ELEVATED
