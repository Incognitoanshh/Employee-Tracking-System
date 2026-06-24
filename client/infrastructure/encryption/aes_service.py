from cryptography.fernet import Fernet
import os


class AESService:

    KEY_FILE = "storage/aes.key"

    @classmethod
    def get_key(cls):

        if not os.path.exists(cls.KEY_FILE):

            os.makedirs("storage", exist_ok=True)

            key = Fernet.generate_key()

            with open(cls.KEY_FILE, "wb") as file:
                file.write(key)

                with open(cls.KEY_FILE, "rb") as file:
                    return file.read()

    @classmethod
    def encrypt_file(cls, file_path):

        key = cls.get_key()

        cipher = Fernet(key)

        with open(file_path, "rb") as file:
            data = file.read()

            encrypted = cipher.encrypt(data)

            encrypted_path = file_path + ".enc"

            with open(encrypted_path, "wb") as file:
                file.write(encrypted)

                return encrypted_path