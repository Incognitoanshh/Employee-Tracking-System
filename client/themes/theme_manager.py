from client.services.settings_service import SettingsService


class ThemeManager:

    @staticmethod
    def current():

        return SettingsService.get_setting(
            "theme",
            "Dark"
        )

    @staticmethod
    def dark():

        return ThemeManager.current() == "Dark"

    @staticmethod
    def background():

        if ThemeManager.dark():
            return "#0b0f19"

        return "#f8fafc"

    @staticmethod
    def card():

        if ThemeManager.dark():
            return "#111827"

        return "#ffffff"

    @staticmethod
    def border():

        if ThemeManager.dark():
            return "#1f2937"

        return "#d1d5db"

    @staticmethod
    def primary_text():

        if ThemeManager.dark():
            return "#ffffff"

        return "#111827"

    @staticmethod
    def secondary_text():

        if ThemeManager.dark():
            return "#94a3b8"

        return "#64748b"