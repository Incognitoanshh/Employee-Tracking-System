from client.infrastructure.database.database import Database


class SettingsService:

    @staticmethod
    def save_setting(
        key,
        value
    ):

        connection = Database.connect()

        cursor = connection.cursor()

        cursor.execute("""

            INSERT OR REPLACE INTO settings(

                key,
                value

            )

            VALUES (?, ?)

        """, (

            key,
            str(value)

        ))

        connection.commit()

        connection.close()

    @staticmethod
    def get_setting(
        key,
        default=None
    ):

        connection = Database.connect()

        cursor = connection.cursor()

        cursor.execute("""

            SELECT value

            FROM settings

            WHERE key=?

        """, (key,))

        result = cursor.fetchone()

        connection.close()

        if result:

            return result[0]

        return default